"""ClickStack / HyperDX dashboard adapter (#365): SQL predicates, the card and
detail shapes, dotted metric names with histogram suffixes, counter increases,
log rows. Fixtures mirror rows read from the all-in-one 2.40.0 image's
ClickHouse (26.8) after one real Hermes turn on 2026-10-11."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from hermes_otel.dashboard import backends
from hermes_otel.dashboard.backends import clickstack as cs
from hermes_otel.dashboard.backends.base import BackendError, LogFilter, StructuredFilter

NS = 1_000_000_000
T0 = 1_791_749_100
TRACE = "763b95305b39f4483336878c5ea06668"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(backends, "top_level_config", lambda: {})
    backends.clear_caches()
    yield
    backends.clear_caches()


def _adapter(**extra: Any) -> cs.ClickstackAdapter:
    return cs.ClickstackAdapter(
        {
            "type": "clickstack",
            "endpoint": "http://localhost:4408",
            "query_port": 8124,
            "user": "api",
            "password": "api",
            **extra,
        }
    )


class _SQL:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: List[str] = []

    def __call__(self, sql: str, timeout: float = 30.0):
        self.calls.append(sql)
        return self.answers.pop(0) if self.answers else []


def _span_row(
    sid: str, name: str, ts_ns: int, dur_ns: int, parent: str = "", **attrs
) -> Dict[str, Any]:
    return {
        "TraceId": TRACE,
        "SpanId": sid,
        "ParentSpanId": parent,
        "SpanName": name,
        "SpanKind": "Internal",
        "ServiceName": "hermes-agent",
        "StatusCode": "Ok",
        "StatusMessage": "",
        "Duration": dur_ns,
        "SpanAttributes": {"hermes.turn.number": "1", **attrs},
        "ResourceAttributes": {"service.instance.id": "inst-1", "openinference.project.name": "p"},
        "ts_ns": ts_ns,
        "event_ts": [],
        "event_names": [],
        "event_attrs": [],
    }


ROOT = _span_row(
    "500ddb2cc9b0dddf",
    "agent",
    T0 * NS,
    4_228_776_000,
    **{
        "hermes.session_id": "20261011_160608_962d72",
        "gen_ai.usage.total_tokens": "23798",
        "llm.model_name": "nvidia/nemotron-3-super-120b-a12b",
        "hermes.session.completed": "true",
    },
)
TOOL = _span_row(
    "aaaa000000000001",
    "tool.terminal",
    T0 * NS + 1_500_000_000,
    1_120_770_000,
    parent="500ddb2cc9b0dddf",
    **{"tool.name": "terminal", "openinference.span.kind": "TOOL"},
)
TOOL["event_ts"] = [T0 * NS + 1_600_000_000]
TOOL["event_names"] = ["tool.started"]
TOOL["event_attrs"] = [{"command": "ls"}]
FAILED = _span_row(
    "aaaa000000000002",
    "tool.read_file",
    T0 * NS + 3_000_000_000,
    3_595_000,
    parent="500ddb2cc9b0dddf",
    **{"tool.name": "read_file"},
)
FAILED["StatusCode"] = "Error"
FAILED["StatusMessage"] = "No such file"
FAILED["SpanKind"] = "Client"


class TestSetup:
    def test_registered_with_every_filter_declared(self):
        assert backends.find_adapter_class("clickstack") is cs.ClickstackAdapter
        assert backends.find_adapter_class("hyperdx") is cs.ClickstackAdapter
        assert set(cs.ClickstackAdapter.filter_support) == set(backends.base.FILTER_KEYS)

    def test_urls_status_and_auth(self):
        a = _adapter()
        assert a.query_url == "http://localhost:8124"
        st = a.status()
        assert st["metrics"] is True and st["logs"] is True
        assert st["database"] == "default" and st["query_url"] == "http://localhost:8124"
        assert a._headers()["Authorization"] == "Basic YXBpOmFwaQ=="

    def test_defaults_without_credentials(self):
        a = cs.ClickstackAdapter({"type": "clickstack", "endpoint": "http://hdx:4318"})
        assert a.query_url == "http://hdx:8123"
        assert "Authorization" not in a._headers()

    def test_trace_url_only_with_ui_url(self):
        assert _adapter().trace_url(TRACE) is None
        assert (
            _adapter(ui_url="http://localhost:3050/").trace_url(TRACE)
            == f"http://localhost:3050/search?q=TraceId%3A{TRACE}"
        )

    def test_bad_metrics_match_is_a_request_error(self):
        with pytest.raises(BackendError) as ei:
            _adapter(metrics_match="(")
        assert ei.value.kind == "request"


class TestSqlHelpers:
    def test_literals(self):
        assert cs.sql_str("it's \\ x") == "'it\\'s \\\\ x'"
        assert cs.sql_like("50%_a'") == "'%50\\%\\_a\\'%'"
        assert cs.sql_ident("otel_traces") == "`otel_traces`"
        with pytest.raises(BackendError):
            cs.sql_ident("otel_traces; DROP")


class TestSearch:
    def test_predicates(self):
        f = StructuredFilter(
            service="svc",
            name_regex="agent",
            name_prefix="tool.",
            attr_equals={"tool.name": "terminal", "hermes.session_id": "s1"},
            min_duration_ms=250,
            status="error",
            free_text="hello",
            raw="SpanAttributes['x'] = '1'",
            before_ns=(T0 + 30) * NS,
        )
        preds = _adapter()._span_predicates(f, T0, T0 + 60)
        assert f"Timestamp >= fromUnixTimestamp64Nano({T0 * NS})" in preds
        assert f"Timestamp < fromUnixTimestamp64Nano({(T0 + 30) * NS})" in preds
        assert "ServiceName = 'svc'" in preds
        assert "ParentSpanId = ''" in preds
        assert "SpanName = 'agent'" in preds
        assert "startsWith(SpanName, 'tool.')" in preds
        assert "SpanAttributes['tool.name'] = 'terminal'" in preds
        assert "Duration >= 250000000" in preds
        assert "StatusCode = 'Error'" in preds
        assert any("input.value'] ILIKE '%hello%'" in p for p in preds)
        assert "(SpanAttributes['x'] = '1')" in preds

    def test_status_ok_and_default_service_and_no_root_predicate(self):
        preds = _adapter()._span_predicates(
            StructuredFilter(status="ok", roots_only=False), T0, T0 + 1
        )
        assert "StatusCode != 'Error'" in preds
        assert "ServiceName = 'hermes-agent'" in preds
        assert "ParentSpanId = ''" not in preds

    def test_card_from_root_with_span_count_and_coerced_tags(self, monkeypatch):
        a = _adapter()
        sql = _SQL([ROOT], [{"TraceId": TRACE, "n": 7}])
        monkeypatch.setattr(a, "_sql", sql)
        page = a.search(StructuredFilter(), T0 - 60, T0 + 60, 10)
        assert "LIMIT 11" in sql.calls[0]
        assert "ORDER BY Timestamp DESC" in sql.calls[0]
        assert "GROUP BY TraceId" in sql.calls[1]
        rows = page["traces"]
        assert len(rows) == 1 and page["has_more"] is False
        row = rows[0]
        assert row["traceID"] == TRACE and row["spanCount"] == 7
        assert row["rootTraceName"] == "agent" and row["rootServiceName"] == "hermes-agent"
        assert row["startTimeUnixNano"] == str(T0 * NS) and row["durationMs"] == 4228
        attrs = {x["key"]: x["value"] for x in row["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["gen_ai.usage.total_tokens"] == {"intValue": "23798"}
        assert attrs["hermes.session_id"] == {"stringValue": "20261011_160608_962d72"}
        assert "status" not in attrs

    def test_widened_search_cards_the_earliest_matching_span_per_trace(self, monkeypatch):
        a = _adapter()
        sql = _SQL([FAILED, TOOL], [{"TraceId": TRACE, "n": 7}])
        monkeypatch.setattr(a, "_sql", sql)
        f = StructuredFilter(name_prefix="tool.", roots_only=False)
        page = a.search(f, T0 - 60, T0 + 60, 10)
        assert "LIMIT 44" in sql.calls[0]  # (limit + 1) * rows per trace
        assert [r["rootTraceName"] for r in page["traces"]] == ["tool.terminal"]
        assert page["traces"][0]["spanCount"] == 7

    def test_error_root_is_flagged(self, monkeypatch):
        a = _adapter()
        bad = dict(ROOT, StatusCode="Error")
        monkeypatch.setattr(a, "_sql", _SQL([bad], [{"TraceId": TRACE, "n": 1}]))
        rows = a.search(StructuredFilter(), T0 - 60, T0 + 60, 10)["traces"]
        attrs = {x["key"]: x["value"] for x in rows[0]["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["status"] == {"stringValue": "error"}

    def test_empty_answer_skips_the_count_query(self, monkeypatch):
        a = _adapter()
        sql = _SQL([])
        monkeypatch.setattr(a, "_sql", sql)
        assert a.search(StructuredFilter(), T0, T0 + 1, 5)["traces"] == []
        assert len(sql.calls) == 1

    def test_paging_cursor_and_has_more(self, monkeypatch):
        a = _adapter()
        older = dict(ROOT, TraceId="1" * 32, ts_ns=(T0 - 100) * NS)
        sql = _SQL([ROOT, older], [{"TraceId": TRACE, "n": 7}, {"TraceId": "1" * 32, "n": 3}])
        monkeypatch.setattr(a, "_sql", sql)
        page = a.search(StructuredFilter(before_ns=(T0 + 1) * NS), T0 - 3600, T0 + 60, 1)
        assert [r["traceID"] for r in page["traces"]] == [TRACE]
        assert page["has_more"] is True and page["next_before_ns"] == T0 * NS


class TestDetail:
    def test_batches_status_events_kinds(self, monkeypatch):
        a = _adapter()
        sql = _SQL([ROOT, TOOL, FAILED])
        monkeypatch.setattr(a, "_sql", sql)
        detail = a.get_trace(TRACE)
        assert f"WHERE TraceId = '{TRACE}'" in sql.calls[0]
        assert detail["span_count"] == 3 and len(detail["batches"]) == 1
        res = {x["key"]: x["value"] for x in detail["batches"][0]["resource"]["attributes"]}
        assert res["service.name"] == {"stringValue": "hermes-agent"}
        assert res["service.instance.id"] == {"stringValue": "inst-1"}
        spans = {s["spanId"]: s for s in detail["batches"][0]["scopeSpans"][0]["spans"]}
        root = spans["500ddb2cc9b0dddf"]
        assert root["parentSpanId"] is None and root["kind"] == 1
        assert root["endTimeUnixNano"] == str(T0 * NS + 4_228_776_000)
        assert root["status"] == {"code": 1, "message": ""}
        tool = spans["aaaa000000000001"]
        assert tool["parentSpanId"] == "500ddb2cc9b0dddf"
        assert tool["events"] == [
            {
                "name": "tool.started",
                "timeUnixNano": str(T0 * NS + 1_600_000_000),
                "attributes": [{"key": "command", "value": {"stringValue": "ls"}}],
            }
        ]
        failed = spans["aaaa000000000002"]
        assert failed["kind"] == 3
        assert failed["status"] == {"code": 2, "message": "No such file"}
        attrs = {x["key"]: x["value"] for x in failed["attributes"]}
        assert attrs["hermes.turn.number"] == {"intValue": "1"}

    def test_unknown_trace_is_not_found(self, monkeypatch):
        a = _adapter()
        monkeypatch.setattr(a, "_sql", _SQL([]))
        with pytest.raises(BackendError) as ei:
            a.get_trace("0" * 32)
        assert ei.value.kind == "not_found" and ei.value.status_code == 404


class TestMetrics:
    def test_names_filtered_and_histograms_split(self, monkeypatch):
        a = _adapter()
        sql = _SQL(
            [
                {"kind": "sum", "MetricName": "hermes.token.usage"},
                {"kind": "gauge", "MetricName": "otelcol_exporter_queue_size"},
                {"kind": "gauge", "MetricName": "system.cpu.utilization"},
                {"kind": "histogram", "MetricName": "hermes.tool.duration"},
            ]
        )
        monkeypatch.setattr(a, "_sql", sql)
        names = [n["name"] for n in a.metric_names(T0, T0 + 60)]
        assert names == [
            "hermes.token.usage",
            "hermes.tool.duration.count",
            "hermes.tool.duration.sum",
            "system.cpu.utilization",
        ]
        assert "UNION ALL" in sql.calls[0] and f"toDateTime({T0})" in sql.calls[0]

    def test_counter_increases_with_lookbehind_and_label_fallback(self, monkeypatch):
        a = _adapter()
        start, end = T0, T0 + 600
        rows = [
            {"ts": start - 120, "v": 100, "Attributes": {"token_type": "input"}, "inst": "gw"},
            {"ts": start + 30, "v": 100, "Attributes": {"token_type": "input"}, "inst": "gw"},
            {"ts": start + 90, "v": 105, "Attributes": {"token_type": "input"}, "inst": "gw"},
            {"ts": start + 400, "v": 17078, "Attributes": {"token_type": "input"}, "inst": "one"},
            {"ts": start + 460, "v": 17078, "Attributes": {"token_type": "input"}, "inst": "one"},
            {"ts": start + 400, "v": 42, "Attributes": {"token_type": "output"}, "inst": "one"},
        ]
        sql = _SQL(rows)
        monkeypatch.setattr(a, "_sql", sql)
        out = a.metrics_query("hermes.token.usage", start, end, 300, group_by="token.type")
        assert out["cumulative"] is True and out["agg"] == "sum"
        # T0 is a multiple of the bucket, so +90 s lands in the first bucket
        # and +400 s in the second
        assert out["series"]["input"] == [5.0, 17078.0, None]
        assert out["series"]["output"] == [None, 42.0, None]
        assert "`otel_metrics_sum`" in sql.calls[0]
        assert f"TimeUnix >= toDateTime({start - cs.LOOKBEHIND_S})" in sql.calls[0]
        assert "MetricName = 'hermes.token.usage'" in sql.calls[0]

    def test_histogram_suffixes_read_the_histogram_columns(self, monkeypatch):
        a = _adapter()
        sql = _SQL([{"ts": T0 + 10, "v": 2, "Attributes": {"tool_name": "terminal"}, "inst": "i"}])
        monkeypatch.setattr(a, "_sql", sql)
        out = a.metrics_query("hermes.tool.duration.count", T0, T0 + 60, 60, group_by="tool.name")
        assert "`otel_metrics_histogram`" in sql.calls[0]
        assert (
            "Count AS v" in sql.calls[0] and "MetricName = 'hermes.tool.duration'" in sql.calls[0]
        )
        assert out["series"] == {"terminal": [2.0, None]}
        sql2 = _SQL([])
        monkeypatch.setattr(a, "_sql", sql2)
        a.metrics_query("hermes.tool.duration.sum", T0, T0 + 60, 60)
        assert "Sum AS v" in sql2.calls[0]

    def test_gauge_fallback_keeps_raw_values(self, monkeypatch):
        a = _adapter()
        sql = _SQL([], [{"ts": T0 + 10, "v": 0.5, "Attributes": {}, "inst": "i"}])
        monkeypatch.setattr(a, "_sql", sql)
        out = a.metrics_query("system.cpu.utilization", T0, T0 + 60, 60, agg="max")
        assert "`otel_metrics_sum`" in sql.calls[0] and "`otel_metrics_gauge`" in sql.calls[1]
        assert "cumulative" not in out and out["series"] == {"_": [0.5, None]}

    def test_bad_metric_name_is_a_request_error(self):
        with pytest.raises(BackendError) as ei:
            _adapter().metrics_query("bad name", T0, T0 + 1, 1)
        assert ei.value.kind == "request"


class TestLogs:
    def test_predicates(self):
        f = LogFilter(
            trace_id="t",
            span_id="s",
            session="sid",
            min_level=30,
            logger="cli",
            text="it's 100%",
            event_name="hermes.turn.end",
            before_ns=(T0 + 30) * NS,
        )
        preds = _adapter()._log_predicates(f, T0, T0 + 60)
        assert f"Timestamp < fromUnixTimestamp64Nano({(T0 + 30) * NS})" in preds
        assert "TraceId = 't'" in preds and "SpanId = 's'" in preds
        assert "LogAttributes['hermes.session_id'] = 'sid'" in preds
        assert "ScopeName = 'cli'" in preds
        assert "SeverityNumber >= 13" in preds
        assert "LogAttributes['event.name'] = 'hermes.turn.end'" in preds
        assert "Body ILIKE '%it\\'s 100\\%%'" in preds
        assert "LogAttributes['event.name'] != ''" in _adapter()._log_predicates(
            LogFilter(events_only=True), T0, T0 + 1
        )

    def test_rows_become_live_store_records(self, monkeypatch):
        a = _adapter()
        rows = [
            {
                "ts_ns": (T0 + 20) * NS,
                "SeverityText": "info",
                "SeverityNumber": 9,
                "ScopeName": "agent.conversation_loop",
                "Body": "Turn ended",
                "TraceId": TRACE,
                "SpanId": "aaaa000000000001",
                "LogAttributes": {
                    "hermes.session_id": "s1",
                    "event.name": "hermes.turn.end",
                    "code.line.number": "42",
                },
            },
            {
                "ts_ns": (T0 + 10) * NS,
                "SeverityText": "warn",
                "SeverityNumber": 13,
                "ScopeName": "cli",
                "Body": "careful",
                "TraceId": "",
                "SpanId": "",
                "LogAttributes": {},
            },
        ]
        sql = _SQL(rows)
        monkeypatch.setattr(a, "_sql", sql)
        out = a.logs_search(LogFilter(), T0, T0 + 60, 50)
        assert f"LIMIT {50 + cs.LOG_PAGE_SLACK}" in sql.calls[0]
        assert [r["body"] for r in out] == ["Turn ended", "careful"]
        first, second = out
        assert first["level"] == "INFO" and first["severity_number"] == 9
        assert first["session_id"] == "s1" and first["event_name"] == "hermes.turn.end"
        assert first["attributes"] == {"code.line.number": 42}
        assert first["trace_id"] == TRACE and first["span_id"] == "aaaa000000000001"
        assert second["level"] == "WARN" and second["trace_id"] is None
        assert second["event_name"] is None and second["session_id"] is None

    def test_loggers(self, monkeypatch):
        a = _adapter()
        sql = _SQL([{"ScopeName": "hermes_otel", "n": 5}, {"ScopeName": "", "n": 9}])
        monkeypatch.setattr(a, "_sql", sql)
        assert a.loggers(T0, T0 + 60) == [{"logger": "hermes_otel", "count": 5}]
        assert "GROUP BY ScopeName" in sql.calls[0]


class TestTransport:
    def test_posts_sql_with_basic_auth_and_unwraps_json(self, monkeypatch):
        import io
        from urllib import request as _urlrequest

        seen: Dict[str, Any] = {}

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=0):
            seen["url"] = req.full_url
            seen["body"] = req.data.decode()
            seen["auth"] = req.get_header("Authorization")
            return _Resp(b'{"meta":[{"name":"n","type":"UInt64"}],"data":[{"n":7}],"rows":1}')

        monkeypatch.setattr(_urlrequest, "urlopen", fake_urlopen)
        assert _adapter()._sql("SELECT 7 AS n") == [{"n": 7}]
        assert seen["url"].startswith("http://localhost:8124/?")
        assert "database=default" in seen["url"]
        assert "output_format_json_quote_64bit_integers=0" in seen["url"]
        assert seen["body"] == "SELECT 7 AS n"
        assert seen["auth"] == "Basic YXBpOmFwaQ=="

    def test_clickhouse_errors_are_backend_errors_not_not_found(self, monkeypatch):
        from urllib import error as _urlerror
        from urllib import request as _urlrequest

        def fake_urlopen(req, timeout=0):
            raise _urlerror.HTTPError(
                req.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(b"Code: 47. DB::Exception: Unknown expression identifier `nope`"),
            )

        import io

        monkeypatch.setattr(_urlrequest, "urlopen", fake_urlopen)
        with pytest.raises(BackendError) as ei:
            _adapter()._sql("SELECT nope")
        assert ei.value.kind == "backend" and "Unknown expression" in ei.value.detail

    def test_auth_failure_is_an_auth_error(self, monkeypatch):
        import io
        from urllib import error as _urlerror
        from urllib import request as _urlrequest

        def fake_urlopen(req, timeout=0):
            raise _urlerror.HTTPError(
                req.full_url, 516, "Auth", {}, io.BytesIO(b"Code: 516. Authentication failed")
            )

        monkeypatch.setattr(_urlrequest, "urlopen", fake_urlopen)
        with pytest.raises(BackendError) as ei:
            _adapter()._sql("SELECT 1")
        assert ei.value.kind == "auth"
