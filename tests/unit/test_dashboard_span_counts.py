"""Backend cards carry the whole-trace span count, or none at all (#179)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

from hermes_otel.dashboard.backends.base import StructuredFilter
from hermes_otel.dashboard.backends.langfuse import LangfuseAdapter, _LangfuseTracesApi
from hermes_otel.dashboard.backends.openobserve import OpenObserveAdapter
from hermes_otel.dashboard.backends.phoenix import PhoenixAdapter


def _dashboard_dir() -> Path:
    from hermes_otel.dashboard import backends as _b

    return Path(_b.__file__).resolve().parent.parent


class TestPhoenix:
    def test_span_count_is_the_trace_total_not_matched_spans(self):
        with patch("hermes_otel.dashboard.backends.top_level_config", return_value={}):
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

        with patch(
            "hermes_otel.dashboard.backends.openobserve.http_post_json", side_effect=fake_post
        ):
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

        with patch(
            "hermes_otel.dashboard.backends.openobserve.http_post_json", side_effect=fake_post
        ):
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
        with patch(
            "hermes_otel.dashboard.backends.langfuse.http_get_json", return_value={"data": [item]}
        ):
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
        with patch(
            "hermes_otel.dashboard.backends.langfuse.http_get_json", return_value={"data": [item]}
        ):
            out = self._adapter().search(StructuredFilter(), 0, 10, 50)
        attrs = {a["key"] for a in out["traces"][0]["spanSets"][0]["spans"][0]["attributes"]}
        assert "hermes.cost.usage" not in attrs
        assert "spanCount" not in out["traces"][0]


def test_lookback_cap_allows_a_year():
    # plugin_api needs the real FastAPI (APIRouter, Query); read the source.
    src = (_dashboard_dir() / "plugin_api.py").read_text(encoding="utf-8")
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
    assert _LangfuseTracesApi(a)._list_params(f, 0, 10, 5)["sessionId"] == "s-9"


def test_backend_cards_carry_the_session_id():
    from hermes_otel.dashboard.backends import openobserve, phoenix, tempo
    from hermes_otel.dashboard.backends.shapes import CARD_ATTRIBUTE_KEYS

    # One table for every adapter (#403); Tempo spells it as a select list.
    assert "hermes.session_id" in CARD_ATTRIBUTE_KEYS
    assert ".hermes.session_id" in tempo._CARD_SELECT_ATTRS
    assert openobserve._CARD_ATTR_OO_COLS == tuple(k.replace(".", "_") for k in CARD_ATTRIBUTE_KEYS)
    assert phoenix.CARD_ATTRIBUTE_SET == frozenset(CARD_ATTRIBUTE_KEYS)
    row = {"operation_name": "agent", "hermes_session_id": "s-1", "hermes_turn_number": "2"}
    attrs = openobserve._row_to_card_attrs(row)
    assert attrs["hermes.session_id"] == "s-1" and attrs["hermes.turn.number"] == 2


class TestTraceUrls:
    def test_phoenix_links_to_the_project_trace_page(self):
        with patch("hermes_otel.dashboard.backends.top_level_config", return_value={}):
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
        with patch(
            "hermes_otel.dashboard.backends.langfuse.http_get_json",
            return_value={"data": [{"id": "proj-1"}]},
        ):
            assert a.trace_url("abc") == "http://localhost:3000/project/proj-1/traces/abc"
        with patch(
            "hermes_otel.dashboard.backends.langfuse.http_get_json",
            side_effect=RuntimeError("down"),
        ):
            b = LangfuseAdapter(
                {
                    "type": "langfuse",
                    "endpoint": "http://localhost:3000",
                    "public_key": "pk",
                    "secret_key": "sk",
                }
            )
            assert b.trace_url("abc") is None

    def test_openobserve_links_to_its_trace_details_page(self):
        a = OpenObserveAdapter(
            {
                "type": "openobserve",
                "endpoint": "http://localhost:5080/api/default/v1/traces",
                "user": "u",
                "password": "p",
            }
        )
        assert a.trace_url("abc").startswith(
            "http://localhost:5080/web/traces/trace-details?org_identifier=default&stream=default&trace_id=abc&from="
        )
