"""Per-request backend selection and adapter capabilities (#177, #182)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_DASHBOARD = Path(__file__).resolve().parent.parent.parent / "hermes_otel" / "dashboard"
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))
import backends  # noqa: E402
import plugin_api  # noqa: E402
from backends.base import bucketize, counter_increases  # noqa: E402

CFG = [
    {"type": "phoenix", "name": "phx", "endpoint": "http://localhost:6006"},
    {
        "type": "openobserve",
        "name": "oo",
        "endpoint": "http://localhost:5080/api/default/v1/traces",
        "user": "u",
        "password": "p",
    },
    {"type": "honeycomb", "name": "hc", "endpoint": "https://api.honeycomb.io"},
]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(backends, "load_config", lambda: (Path("/x/hermes_otel.yaml"), CFG, "oo"))
    monkeypatch.setattr("backends.top_level_config", lambda: {})
    app = FastAPI()
    app.include_router(plugin_api.router)
    with TestClient(app) as c:
        yield c


class TestResolveAdapter:
    def test_pin_then_first_supported(self, monkeypatch):
        monkeypatch.setattr(backends, "load_config", lambda: (None, CFG, "oo"))
        with patch("backends.top_level_config", return_value={}):
            a, _, _, pin = backends.resolve_adapter()
            assert a.cfg["name"] == "oo" and pin == "oo"
            monkeypatch.setattr(backends, "load_config", lambda: (None, CFG, None))
            a, _, _, _ = backends.resolve_adapter()
            assert a.cfg["name"] == "phx"

    def test_by_name_and_by_type(self, monkeypatch):
        monkeypatch.setattr(backends, "load_config", lambda: (None, CFG, "oo"))
        with patch("backends.top_level_config", return_value={}):
            assert backends.resolve_adapter("phx")[0].cfg["name"] == "phx"
            assert backends.resolve_adapter("phoenix")[0].cfg["name"] == "phx"
            # configured but no adapter for the type
            assert backends.resolve_adapter("hc")[0] is None
            with pytest.raises(KeyError) as e:
                backends.resolve_adapter("nope")
            assert "phx, oo, hc" in str(e.value)


class TestStatusAndRoutes:
    def test_status_lists_capabilities_and_active(self, client):
        st = client.get("/status").json()
        assert st["active"] == "oo" and st["query_backend_pin"] == "oo"
        by = {b["name"]: b for b in st["available"]}
        assert by["phx"] == {
            "type": "phoenix",
            "name": "phx",
            "endpoint": "http://localhost:6006",
            "supported": True,
            "metrics": False,
            "logs": False,
        }
        assert by["oo"]["metrics"] and by["oo"]["logs"] and by["oo"]["supported"]
        assert by["hc"] == {
            "type": "honeycomb",
            "name": "hc",
            "endpoint": "https://api.honeycomb.io",
            "supported": False,
            "metrics": False,
            "logs": False,
        }
        assert st["metrics"] is True and st["logs"] is True

    def test_status_for_a_chosen_backend(self, client):
        st = client.get("/status", params={"backend": "phx"}).json()
        assert st["active"] == "phx" and st["type"] == "phoenix" and st["metrics"] is False

    def test_unknown_backend_is_400_and_unsupported_is_503(self, client):
        r = client.get("/status", params={"backend": "nope"})
        assert r.status_code == 400 and "phx, oo, hc" in r.json()["detail"]
        r = client.get("/traces/search", params={"backend": "hc"})
        assert r.status_code == 503 and "no dashboard adapter" in r.json()["detail"]

    def test_capability_gating(self, client):
        r = client.get("/metrics/names", params={"backend": "phx"})
        assert r.status_code == 503 and "does not serve metrics" in r.json()["detail"]
        r = client.get("/logs/search", params={"backend": "phx"})
        assert r.status_code == 503 and "does not serve logs" in r.json()["detail"]

    def test_search_goes_to_the_chosen_adapter(self, client):
        with patch(
            "backends.phoenix.PhoenixAdapter.search",
            return_value={"traces": [{"traceID": "t", "spanCount": 3}]},
        ) as m:
            r = client.get("/traces/search", params={"backend": "phx", "lookback_hours": 2})
        assert r.status_code == 200 and r.json()["traces"][0]["spanCount"] == 3
        f, start, end, limit = m.call_args[0]
        assert end - start == 7200 and limit == 50

    def test_backend_metrics_and_logs_routes(self, client):
        fake = {
            "name": "hermes_token_usage",
            "agg": "sum",
            "bucketS": 60,
            "buckets": [0],
            "series": {"input": [5.0]},
            "points": 1,
        }
        with (
            patch("backends.openobserve.OpenObserveAdapter.metrics_query", return_value=fake),
            patch(
                "backends.openobserve.OpenObserveAdapter.metric_names",
                return_value=[{"name": "hermes_token_usage", "count": 2}],
            ),
            patch(
                "backends.openobserve.OpenObserveAdapter.logs_search",
                return_value=[{"level": "INFO", "logger": "x", "body": "b", "time_unix_nano": 1}],
            ),
            patch(
                "backends.openobserve.OpenObserveAdapter.loggers",
                return_value=[{"logger": "x", "count": 1}],
            ),
        ):
            assert (
                client.get("/metrics/names", params={"backend": "oo"}).json()["names"][0]["name"]
                == "hermes_token_usage"
            )
            q = client.get(
                "/metrics/query",
                params={"backend": "oo", "name": "hermes_token_usage", "group_by": "token_type"},
            ).json()
            assert q["backend"] == "oo" and q["series"] == {"input": [5.0]}
            assert (
                client.get("/logs/search", params={"backend": "oo", "min_level": 30}).json()[
                    "logs"
                ][0]["body"]
                == "b"
            )
            assert client.get("/loggers", params={"backend": "oo"}).json()["loggers"] == [
                {"logger": "x", "count": 1}
            ]
            assert (
                client.get(
                    "/metrics/query", params={"backend": "oo", "name": "x", "agg": "median"}
                ).status_code
                == 422
            )


class TestBucketHelpers:
    def test_counter_increases_per_series_and_reset(self):
        samples = [(1, 10.0, "a"), (2, 15.0, "a"), (3, 3.0, "a"), (1, 100.0, "b"), (2, 100.0, "b")]
        assert counter_increases(samples) == [(2, 5.0, "a"), (3, 3.0, "a"), (2, 0.0, "b")]

    def test_bucketize_matches_the_live_store_shape(self):
        s = 1_020_000_000_000  # a multiple of the 60 s bucket
        out = bucketize(
            [(s + 5e9, 1, "x"), (s + 65e9, 2, "x"), (s + 70e9, 4, "y")], s, s + 120e9, 60
        )
        assert out["buckets"] == [s, s + 60e9, s + 120e9]
        assert out["series"] == {"x": [1.0, 2.0, None], "y": [None, 4.0, None]}
        assert out["points"] == 3 and out["bucketS"] == 60
        assert bucketize([(s, 1, "x"), (s, 3, "x")], s, s, 60, agg="avg")["series"] == {"x": [2.0]}


class TestOpenObserveMetrics:
    def test_cumulative_rows_become_increases_grouped_by_label(self):
        from backends.openobserve import OpenObserveAdapter

        a = OpenObserveAdapter(CFG[1])
        base_us = 1_700_000_000_000_000
        rows = [
            {
                "_timestamp": base_us + i * 15_000_000,
                "value": v,
                "token_type": t,
                "model": "m",
                "aggregation_temporality": "AGGREGATION_TEMPORALITY_CUMULATIVE",
                "is_monotonic": "true",
                "__name__": "hermes_token_usage",
            }
            for i, (v, t) in enumerate(
                [(100, "input"), (10, "output"), (250, "input"), (16, "output"), (400, "input")]
            )
        ]
        with patch.object(a, "_search", return_value=rows):
            out = a.metrics_query(
                "hermes_token_usage",
                base_us // 1_000_000,
                base_us // 1_000_000 + 60,
                60,
                group_by="token_type",
            )
        assert out["cumulative"] is True
        assert sum(v for v in out["series"]["input"] if v) == 300.0  # 250-100 + 400-250
        assert sum(v for v in out["series"]["output"] if v) == 6.0

    def test_log_rows_map_to_the_live_shape_and_level_filter(self):
        from backends.base import LogFilter
        from backends.openobserve import OpenObserveAdapter

        a = OpenObserveAdapter(CFG[1])
        rows = [
            {
                "_timestamp": 1_700_000_000_000_000,
                "severity": "ERROR",
                "body": "boom",
                "instrumentation_library_name": "tools.terminal",
                "trace_id": "t1",
            },
            {
                "_timestamp": 1_700_000_000_000_001,
                "severity": "INFO",
                "body": "fine",
                "instrumentation_library_name": "agent",
            },
        ]
        with patch.object(a, "_search", return_value=rows) as m:
            out = a.logs_search(LogFilter(min_level=30, text="o"), 0, 10, 50)
            sql = m.call_args[0][0]
        assert out == [
            {
                "level": "ERROR",
                "logger": "tools.terminal",
                "body": "boom",
                "time_unix_nano": 1_700_000_000_000_000_000,
                "trace_id": "t1",
                "session_id": None,
            }
        ]
        assert "body LIKE '%o%'" in sql and 'FROM "default"' in sql


def test_trace_detail_carries_the_backend_ui_link(client):
    with (
        patch("backends.phoenix.PhoenixAdapter.get_trace", return_value={"batches": []}),
        patch(
            "backends.phoenix.PhoenixAdapter.trace_url",
            return_value="http://localhost:6006/projects/P/traces/abc",
        ),
    ):
        r = client.get("/traces/abc", params={"backend": "phx"}).json()
    assert r["ui_url"] == "http://localhost:6006/projects/P/traces/abc"
