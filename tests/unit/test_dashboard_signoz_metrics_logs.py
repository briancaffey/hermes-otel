"""SigNoz dashboard adapter: metrics and logs (#194).

Recorded from SigNoz v0.143.0 (``/api/v4/query_range`` and
``/api/v3/autocomplete/aggregate_attributes``) against a real Hermes turn.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from hermes_otel.dashboard.backends import signoz as sz
from hermes_otel.dashboard.backends.base import LogFilter

CFG = {
    "type": "signoz",
    "endpoint": "https://signoz.lan/v1/traces",
    "api_key": "k",
    "query_port": 443,
}

CATALOG = {
    "status": "success",
    "data": {
        "attributeKeys": [
            {"key": "hermes.token.usage", "dataType": "float64", "type": "Sum", "isColumn": True},
            {"key": "hermes.message.count", "dataType": "float64", "type": "Sum", "isColumn": True},
            {"key": "hermes.tool.duration.bucket", "dataType": "float64", "type": "Histogram"},
            {"key": "hermes.tool.duration.sum", "dataType": "float64", "type": "Sum"},
            {"key": "hermes.tool.duration.count", "dataType": "float64", "type": "Sum"},
            {"key": "hermes.tool.duration.max", "dataType": "float64", "type": "Gauge"},
            {"key": "hermes.tool.duration.min", "dataType": "float64", "type": "Gauge"},
        ]
    },
}

# hermes.token.usage, increase per hour, grouped by model (two series).
METRICS_GRAPH = {
    "status": "success",
    "data": {
        "resultType": "",
        "result": [
            {
                "queryName": "A",
                "series": [
                    {
                        "labels": {"model": "nvidia/nemotron-3-nano-omni"},
                        "labelsArray": [{"model": "nvidia/nemotron-3-nano-omni"}],
                        "values": [{"timestamp": 1790294400000, "value": "53739"}],
                    },
                    {
                        "labels": {"model": "nvidia/nemotron-3-super"},
                        "labelsArray": [{"model": "nvidia/nemotron-3-super"}],
                        "values": [
                            {"timestamp": 1790294400000, "value": "79423"},
                            {"timestamp": 1790298000000, "value": "100"},
                        ],
                    },
                ],
            }
        ],
    },
}

LOG_ENTRY = {
    "timestamp": "2026-09-25T00:34:58.804Z",
    "data": {
        "attributes_bool": {},
        "attributes_number": {"code.line.number": 173},
        "attributes_string": {
            "code.function.name": "finalize",
            "hermes.session_id": "20260924_203458_4a4dea",
        },
        "body": "[hermes-otel] session 20260924_203458_4a4dea finalized",
        "id": "0lvp5V2AsfACTj1VXzRqsSwZLbk",
        "resources_string": {"service.name": "hermes-agent"},
        "scope_name": "hermes_otel",
        "scope_string": {},
        "severity_number": 9,
        "severity_text": "INFO",
        "span_id": "",
        "trace_id": "aa6bcb4670a7a8a549e55acba84d4f37",
        "trace_flags": 0,
    },
}
LOGS_LIST = {"status": "success", "data": {"result": [{"queryName": "A", "list": [LOG_ENTRY]}]}}

LOGGERS_TABLE = {
    "status": "success",
    "data": {
        "result": [
            {
                "queryName": "A",
                "series": [
                    {
                        "labels": {"scope_name": "gateway.run"},
                        "values": [{"timestamp": 0, "value": "38"}],
                    },
                    {
                        "labels": {"scope_name": "hermes_otel"},
                        "values": [{"timestamp": 0, "value": "73"}],
                    },
                    {"labels": {}, "values": [{"timestamp": 0, "value": "5"}]},
                ],
            }
        ]
    },
}


@pytest.fixture()
def fake_http(monkeypatch):
    """Route the adapter's HTTP calls to recorded responses; keep the request bodies."""
    calls: Dict[str, List[Any]] = {"get": [], "post": []}

    def fake_get(url, headers=None, timeout=10.0):
        calls["get"].append((url, headers))
        assert "autocomplete/aggregate_attributes" in url
        return CATALOG

    def fake_post(url, body, headers=None, timeout=10.0):
        calls["post"].append((url, body, headers))
        source = body["compositeQuery"]["builderQueries"]["A"]["dataSource"]
        panel = body["compositeQuery"]["panelType"]
        if source == "metrics":
            return METRICS_GRAPH
        return LOGS_LIST if panel == "list" else LOGGERS_TABLE

    monkeypatch.setattr(sz, "http_get_json", fake_get)
    monkeypatch.setattr(sz, "http_post_json", fake_post)
    return calls


@pytest.fixture()
def adapter():
    return sz.SigNozAdapter(CFG)


class TestCapabilities:
    def test_status_advertises_metrics_and_logs(self, adapter):
        st = adapter.status()
        assert st["metrics"] is True and st["logs"] is True
        assert st["query_url"] == "https://signoz.lan:443"

    def test_no_api_key_fails_closed(self):
        a = sz.SigNozAdapter({"type": "signoz", "endpoint": "http://localhost:4318/v1/traces"})
        with pytest.raises(sz.HTTPException):
            a.metric_names(0, 1)


class TestMetrics:
    def test_names_skip_histogram_internals_and_are_sorted(self, adapter, fake_http):
        names = [n["name"] for n in adapter.metric_names(0, 3600)]
        assert names == [
            "hermes.message.count",
            "hermes.token.usage",
            "hermes.tool.duration.count",
            "hermes.tool.duration.sum",
        ]
        url, headers = fake_http["get"][0]
        assert headers == {"SIGNOZ-API-KEY": "k"}
        assert "dataSource=metrics" in url

    def test_counter_query_asks_for_increase_and_buckets_on_the_shared_grid(
        self, adapter, fake_http
    ):
        start, end = 1790294400 - 3600, 1790298000 + 3600
        out = adapter.metrics_query(
            "hermes.token.usage", start, end, 3600, group_by="model", agg="sum"
        )
        _url, body, _h = fake_http["post"][-1]
        q = body["compositeQuery"]["builderQueries"]["A"]
        assert body["compositeQuery"]["panelType"] == "graph"
        assert q["aggregateAttribute"] == {
            "key": "hermes.token.usage",
            "dataType": "float64",
            "type": "Sum",
            "isColumn": True,
        }
        assert (q["timeAggregation"], q["spaceAggregation"], q["stepInterval"]) == (
            "increase",
            "sum",
            3600,
        )
        assert q["groupBy"] == [{"key": "model", "type": "tag", "dataType": "string"}]
        assert (body["start"], body["end"], body["step"]) == (start * 1000, end * 1000, 3600)

        assert out["name"] == "hermes.token.usage" and out["kind"] == "Sum" and out["agg"] == "sum"
        assert out["bucketS"] == 3600 and len(out["buckets"]) == 4
        assert out["series"]["nvidia/nemotron-3-nano-omni"] == [None, 53739.0, None, None]
        assert out["series"]["nvidia/nemotron-3-super"] == [None, 79423.0, 100.0, None]
        assert out["points"] == 3

    def test_gauge_and_other_aggregates_map_to_builder_verbs(self, adapter, fake_http):
        adapter.metrics_query("hermes.tool.duration.max", 0, 7200, 60, agg="avg")
        q = fake_http["post"][-1][1]["compositeQuery"]["builderQueries"]["A"]
        assert q["aggregateAttribute"]["type"] == "Gauge"
        assert (q["timeAggregation"], q["spaceAggregation"], q["groupBy"]) == ("avg", "avg", [])

        adapter.metrics_query("hermes.token.usage", 0, 7200, 60, agg="last")
        q = fake_http["post"][-1][1]["compositeQuery"]["builderQueries"]["A"]
        assert (q["timeAggregation"], q["spaceAggregation"]) == ("latest", "sum")

    def test_ungrouped_series_use_the_default_label(self, adapter, fake_http):
        out = adapter.metrics_query("hermes.token.usage", 1790294400 - 60, 1790298000 + 60, 3600)
        assert set(out["series"]) == {"_"}


class TestLogs:
    def test_search_builds_filters_and_normalises_records(self, adapter, fake_http):
        f = LogFilter(
            trace_id="aa6bcb4670a7a8a549e55acba84d4f37",
            session="20260924_203458_4a4dea",
            min_level=30,
            logger="hermes_otel",
            text="finalized",
        )
        logs = adapter.logs_search(f, 0, 3600, 50)
        _url, body, _h = fake_http["post"][-1]
        q = body["compositeQuery"]["builderQueries"]["A"]
        assert (q["dataSource"], q["aggregateOperator"], q["limit"]) == ("logs", "noop", 50)
        assert q["orderBy"] == [{"columnName": "timestamp", "order": "desc"}]
        items = {i["key"]["key"]: (i["op"], i["value"], i["key"]) for i in q["filters"]["items"]}
        assert items["trace_id"][:2] == ("=", "aa6bcb4670a7a8a549e55acba84d4f37")
        assert items["trace_id"][2]["isColumn"] is True
        assert items["hermes.session_id"][:2] == ("=", "20260924_203458_4a4dea")
        assert items["hermes.session_id"][2]["type"] == "tag"
        assert items["scope_name"][:2] == ("=", "hermes_otel")
        assert items["severity_number"][:2] == (">=", 13)  # WARNING → OTel WARN
        assert items["body"][:2] == ("contains", "finalized")

        assert logs == [
            {
                "level": "INFO",
                "logger": "hermes_otel",
                "body": "[hermes-otel] session 20260924_203458_4a4dea finalized",
                "time_unix_nano": 1790296498804000000,
                "trace_id": "aa6bcb4670a7a8a549e55acba84d4f37",
                "session_id": "20260924_203458_4a4dea",
            }
        ]

    def test_empty_filter_sends_no_items(self, adapter, fake_http):
        adapter.logs_search(LogFilter(), 0, 60, 10)
        assert (
            fake_http["post"][-1][1]["compositeQuery"]["builderQueries"]["A"]["filters"]["items"]
            == []
        )

    def test_severity_floor_per_python_level(self):
        assert [sz._otel_severity_for(l) for l in (10, 20, 25, 30, 40, 50, 0)] == [
            5,
            9,
            9,
            13,
            17,
            21,
            1,
        ]

    def test_loggers_group_by_scope_name_and_drop_the_unnamed(self, adapter, fake_http):
        out = adapter.loggers(0, 3600)
        q = fake_http["post"][-1][1]["compositeQuery"]["builderQueries"]["A"]
        assert (q["dataSource"], q["aggregateOperator"], q["reduceTo"]) == ("logs", "count", "sum")
        assert q["groupBy"][0]["key"] == "scope_name" and q["groupBy"][0]["isColumn"] is True
        assert out == [
            {"logger": "hermes_otel", "count": 73},
            {"logger": "gateway.run", "count": 38},
        ]

    def test_missing_fields_do_not_break_a_record(self):
        rec = sz._log_record({"data": {"body": "x"}})
        assert rec == {
            "level": "INFO",
            "logger": "",
            "body": "x",
            "time_unix_nano": 0,
            "trace_id": None,
            "session_id": None,
        }
