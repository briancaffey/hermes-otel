"""GreptimeDB dashboard adapter (#363): the Jaeger-compatible API under
``/v1/jaeger``, SQL-backed metrics with first-sample counting, SQL-backed logs
with bracket JSON paths. Shapes recorded from greptime/greptimedb v1.2.1 after
one real Hermes turn on 2026-10-11."""

from __future__ import annotations

from typing import Any, Dict, List
from urllib import parse as _urlparse

import pytest

from hermes_otel.dashboard import backends
from hermes_otel.dashboard.backends import greptime as gp
from hermes_otel.dashboard.backends import jaeger
from hermes_otel.dashboard.backends.base import BackendError, LogFilter, StructuredFilter

NS = 1_000_000_000
T0 = 1_791_746_900  # window start (s)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(backends, "top_level_config", lambda: {})
    backends.clear_caches()
    jaeger._API_PROBE_CACHE.clear()
    gp._COLUMNS_CACHE.clear()
    yield
    backends.clear_caches()
    jaeger._API_PROBE_CACHE.clear()
    gp._COLUMNS_CACHE.clear()


def _adapter(**extra: Any) -> gp.GreptimeAdapter:
    return gp.GreptimeAdapter({"type": "greptime", "endpoint": "http://localhost:4398", **extra})


class _SQL:
    """Scripted answers for ``_sql``: ``(columns, rows)`` per call, in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: List[str] = []

    def __call__(self, sql: str, timeout: float = 30.0):
        self.calls.append(sql)
        return self.answers.pop(0) if self.answers else ([], [])


class TestSetup:
    def test_registered_with_every_filter_declared(self):
        assert backends.find_adapter_class("greptime") is gp.GreptimeAdapter
        assert set(gp.GreptimeAdapter.filter_support) == set(backends.base.FILTER_KEYS)

    def test_urls_and_status(self):
        a = _adapter()
        assert a.query_url == "http://localhost:4398"
        assert a.api_base == "http://localhost:4398/v1/jaeger"
        assert a.query_api == "v1"
        st = a.status()
        assert st["metrics"] is True and st["logs"] is True
        assert st["query_api"] == "jaeger-compatible"
        assert st["sql_url"] == "http://localhost:4398/v1/sql?db=public"
        assert st["prometheus_url"] == "http://localhost:4398/v1/prometheus"
        assert st["db"] == "public"

    def test_default_port_and_db_and_table(self):
        a = gp.GreptimeAdapter(
            {
                "type": "greptime",
                "endpoint": "http://greptime",
                "db": "otel",
                "logs_table": "my_logs",
            }
        )
        assert a.query_url == "http://greptime:4000"
        assert a.sql_url == "http://greptime:4000/v1/sql?db=otel"
        assert a.logs_table == "my_logs"

    def test_query_path_override(self):
        a = _adapter(query_path="/proxy/jaeger/")
        assert a.api_base == "http://localhost:4398/proxy/jaeger"

    def test_no_trace_url_without_a_documented_route(self):
        assert _adapter().trace_url("abc") is None

    def test_basic_auth_from_env(self, monkeypatch):
        monkeypatch.setenv("GU", "u")
        monkeypatch.setenv("GP", "p")
        a = _adapter(user_env="GU", password_env="GP")
        assert a._headers() == {"Authorization": "Basic dTpw"}

    def test_no_credentials_no_header(self):
        assert _adapter()._headers() == {}


class TestSqlHelpers:
    def test_sql_str_escapes_quotes_and_backslashes(self):
        assert gp.sql_str("it's a \\ test") == "'it''s a \\\\ test'"

    def test_sql_like_escapes_wildcards(self):
        assert gp.sql_like("50%_done'") == "'%50\\%\\_done''%'"

    def test_sql_ident_quotes_and_refuses_junk(self):
        assert gp.sql_ident("hermes_token_usage_total") == '"hermes_token_usage_total"'
        with pytest.raises(BackendError) as ei:
            gp.sql_ident('x"; DROP TABLE y; --')
        assert ei.value.kind == "request"

    def test_json_key_is_a_bracket_path(self):
        assert gp.json_key("hermes.session_id") == "'[\"hermes.session_id\"]'"


class TestTracesViaJaegerPrefix:
    def test_search_and_detail_hit_the_prefixed_api(self, monkeypatch):
        calls: List[str] = []

        def fake(url, headers=None, timeout=10.0):
            calls.append(url)
            return {"data": []}

        monkeypatch.setattr(jaeger, "http_get_json", fake)
        a = _adapter()
        a.search(StructuredFilter(), T0, T0 + 60, 5)
        assert calls[0].startswith("http://localhost:4398/v1/jaeger/api/traces?")
        q = _urlparse.parse_qs(_urlparse.urlsplit(calls[0]).query)
        assert q["service"] == ["hermes-agent"]
        # v1 grammar, never a v3 probe
        assert not any("/api/v3/" in c or "/api/services" in c for c in calls)
        with pytest.raises(BackendError) as ei:
            a.get_trace("t1")  # an empty answer is not_found, through the same prefix
        assert ei.value.kind == "not_found"
        assert calls[-1] == "http://localhost:4398/v1/jaeger/api/traces/t1"


class TestSearchFetch:
    def test_greptime_over_fetches_because_limit_truncates_arbitrarily(self, monkeypatch):
        """GreptimeDB's ``/api/traces?limit=N`` keeps N arbitrary traces, not the
        newest (verified on v1.2.1: ``limit=2`` dropped the newest of five), so
        the adapter asks for ``search_fetch`` and cuts the page itself."""
        calls: List[str] = []
        monkeypatch.setattr(
            jaeger,
            "http_get_json",
            lambda url, headers=None, timeout=10.0: calls.append(url) or {"data": []},
        )
        _adapter().search(StructuredFilter(), T0, T0 + 60, 2)
        q = _urlparse.parse_qs(_urlparse.urlsplit(calls[0]).query)
        assert q["limit"] == [str(gp._DEFAULT_SEARCH_FETCH)]
        calls.clear()
        _adapter(search_fetch=50).search(StructuredFilter(), T0, T0 + 60, 2)
        assert _urlparse.parse_qs(_urlparse.urlsplit(calls[0]).query)["limit"] == ["50"]

    def test_jaeger_itself_still_asks_for_one_beyond_the_page(self, monkeypatch):
        calls: List[str] = []
        monkeypatch.setattr(
            jaeger,
            "http_get_json",
            lambda url, headers=None, timeout=10.0: calls.append(url) or {"data": []},
        )
        jaeger.JaegerAdapter(
            {"type": "jaeger", "endpoint": "http://localhost:16686", "query_api": "v1"}
        ).search(StructuredFilter(), T0, T0 + 60, 2)
        assert _urlparse.parse_qs(_urlparse.urlsplit(calls[0]).query)["limit"] == ["3"]


COUNTER_COLS = [
    "greptime_timestamp",
    "greptime_value",
    "model",
    "service_instance_id",
    "token_type",
]


def _row(ts_ms: int, value: float, model: str, inst: str, token_type: str) -> List[Any]:
    return [ts_ms, value, model, inst, token_type]


class TestMetrics:
    def test_metric_names_use_the_prometheus_label_route(self, monkeypatch):
        calls: List[str] = []
        monkeypatch.setattr(
            gp,
            "_prom_metric_names",
            lambda base, s, e, match, headers=None: calls.append((base, match)) or [{"name": "x"}],
        )
        assert _adapter(metrics_match='{__name__=~"hermes_.*"}').metric_names(T0, T0 + 60) == [
            {"name": "x"}
        ]
        assert calls == [("http://localhost:4398/v1/prometheus", '{__name__=~"hermes_.*"}')]

    def test_counter_increases_from_raw_samples_with_lookbehind(self, monkeypatch):
        a = _adapter()
        start, end = T0, T0 + 600
        sql = _SQL(
            (["column_name"], [[c] for c in COUNTER_COLS]),
            (
                COUNTER_COLS,
                [
                    # a gateway that existed before the window: baseline 100, grows by 5
                    _row((start - 120) * 1000, 100, "m", "gw", "input"),
                    _row((start + 30) * 1000, 100, "m", "gw", "input"),
                    _row((start + 90) * 1000, 105, "m", "gw", "input"),
                    # a one-shot run born inside the window: its value counts in full
                    _row((start + 400) * 1000, 17078, "m", "one-shot", "input"),
                    _row((start + 460) * 1000, 17078, "m", "one-shot", "input"),
                    # another label value
                    _row((start + 400) * 1000, 42, "m", "one-shot", "output"),
                ],
            ),
        )
        monkeypatch.setattr(a, "_sql", sql)
        out = a.metrics_query("hermes_token_usage_total", start, end, 300, group_by="token_type")
        assert out["cumulative"] is True
        assert out["agg"] == "sum" and out["name"] == "hermes_token_usage_total"
        # the grid is aligned down to a multiple of the bucket, so the window
        # spans three buckets; +90 s lands in the first, +400 s in the third
        assert out["series"]["input"] == [5.0, None, 17078.0]
        assert out["series"]["output"] == [None, None, 42.0]
        # the SELECT names the table, every tag column and the look-behind bound
        assert sql.calls[1].startswith('SELECT "greptime_timestamp", "greptime_value", "model"')
        assert f">= {(start - gp.LOOKBEHIND_S) * 1000}::TIMESTAMP_MS" in sql.calls[1]
        assert f"< {end * 1000}::TIMESTAMP_MS" in sql.calls[1]
        assert 'FROM "hermes_token_usage_total"' in sql.calls[1]

    def test_gauge_keeps_raw_values_and_dotted_label_maps_to_a_column(self, monkeypatch):
        a = _adapter()
        start, end = T0, T0 + 600
        cols = ["greptime_timestamp", "greptime_value", "tool_name"]
        sql = _SQL(
            (["column_name"], [[c] for c in cols]),
            (
                cols,
                [[(start + 10) * 1000, 0.5, "terminal"], [(start + 20) * 1000, 0.25, "terminal"]],
            ),
        )
        monkeypatch.setattr(a, "_sql", sql)
        out = a.metrics_query(
            "system_cpu_utilization", start, end, 300, group_by="tool.name", agg="max"
        )
        assert "cumulative" not in out
        assert out["series"] == {"terminal": [0.5, None, None]}
        assert f">= {start * 1000}::TIMESTAMP_MS" in sql.calls[1]  # no look-behind for gauges

    def test_unknown_label_collapses_to_one_series(self, monkeypatch):
        a = _adapter()
        cols = ["greptime_timestamp", "greptime_value", "model"]
        sql = _SQL((["column_name"], [[c] for c in cols]), (cols, [[(T0 + 10) * 1000, 3.0, "m"]]))
        monkeypatch.setattr(a, "_sql", sql)
        out = a.metrics_query("hermes_tool_duration_milliseconds_count", T0, T0 + 60, 60, "nope")
        assert list(out["series"]) == ["_"]

    def test_missing_table_is_an_empty_answer(self, monkeypatch):
        a = _adapter()
        sql = _SQL((["column_name"], []))
        monkeypatch.setattr(a, "_sql", sql)
        out = a.metrics_query("no_such_total", T0, T0 + 60, 60)
        assert out["series"] == {} and out["points"] == 0 and out["sql"] is None
        assert len(sql.calls) == 1

    def test_columns_are_cached_per_table(self, monkeypatch):
        a = _adapter()
        cols = ["greptime_timestamp", "greptime_value"]
        sql = _SQL((["column_name"], [[c] for c in cols]), (cols, []), (cols, []))
        monkeypatch.setattr(a, "_sql", sql)
        a.metrics_query("hermes_session_count_total", T0, T0 + 60, 60)
        a.metrics_query("hermes_session_count_total", T0, T0 + 60, 60)
        assert sum("information_schema" in c for c in sql.calls) == 1

    def test_bad_names_are_request_errors(self):
        with pytest.raises(BackendError) as ei:
            _adapter().metrics_query("bad name", T0, T0 + 60, 60)
        assert ei.value.kind == "request"


LOG_COLS = [
    "timestamp",
    "severity_text",
    "severity_number",
    "scope_name",
    "body",
    "trace_id",
    "span_id",
    "log_attributes",
]


def _log(ts_ns: int, body: str, level="INFO", num=9, attrs=None) -> List[Any]:
    return [
        ts_ns,
        level,
        num,
        "agent.conversation_loop",
        body,
        "0f47d4e859bc1b1817bbfc6bd3f6f15d",
        "a6728ac1c6f4fad6",
        (
            attrs
            if attrs is not None
            else {
                "code.file.path": "/x.py",
                "hermes.session_id": "20261011_152832_f9204a",
                "gen_ai.conversation.id": "20261011_152832_f9204a",
            }
        ),
    ]


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
        sql = " AND ".join(_adapter()._log_predicates(f, T0, T0 + 60))
        assert f'"timestamp" >= {T0 * NS}::TIMESTAMP_NS' in sql
        assert f'"timestamp" < {(T0 + 30) * NS}::TIMESTAMP_NS' in sql  # the cursor tightens the end
        assert "\"trace_id\" = 't'" in sql and "\"span_id\" = 's'" in sql
        assert "json_get_string(\"log_attributes\", '[\"hermes.session_id\"]') = 'sid'" in sql
        assert "\"scope_name\" = 'cli'" in sql
        assert '"severity_number" >= 13' in sql
        assert "json_get_string(\"log_attributes\", '[\"event.name\"]') = 'hermes.turn.end'" in sql
        assert "\"body\" LIKE '%it''s 100\\%%'" in sql

    def test_events_only_predicate(self):
        sql = " AND ".join(_adapter()._log_predicates(LogFilter(events_only=True), T0, T0 + 60))
        assert 'json_get_string("log_attributes", \'["event.name"]\') IS NOT NULL' in sql

    def test_rows_become_live_store_records(self, monkeypatch):
        a = _adapter()
        sql = _SQL(
            (
                LOG_COLS,
                [
                    _log(
                        (T0 + 20) * NS,
                        "Turn ended",
                        attrs={"event.name": "hermes.turn.end", "k": 1},
                    ),
                    _log((T0 + 10) * NS, "hello", level="WARN", num=13),
                ],
            )
        )
        monkeypatch.setattr(a, "_sql", sql)
        rows = a.logs_search(LogFilter(), T0, T0 + 60, 50)
        assert sql.calls[0].startswith('SELECT "timestamp", "severity_text"')
        assert 'FROM "opentelemetry_logs" WHERE' in sql.calls[0]
        assert f"LIMIT {50 + gp.LOG_PAGE_SLACK}" in sql.calls[0]
        assert [r["body"] for r in rows] == ["Turn ended", "hello"]
        first = rows[0]
        assert first["time_unix_nano"] == (T0 + 20) * NS
        assert first["trace_id"] == "0f47d4e859bc1b1817bbfc6bd3f6f15d"
        assert first["span_id"] == "a6728ac1c6f4fad6"
        assert first["event_name"] == "hermes.turn.end"
        assert first["session_id"] is None
        assert first["attributes"] == {"k": 1}
        second = rows[1]
        assert second["level"] == "WARN" and second["severity_number"] == 13
        assert second["session_id"] == "20261011_152832_f9204a"
        assert second["event_name"] is None
        assert "hermes.session_id" not in second["attributes"]
        assert second["attributes"]["code.file.path"] == "/x.py"

    def test_cursor_drops_rows_at_or_after_it(self, monkeypatch):
        a = _adapter()
        sql = _SQL((LOG_COLS, [_log((T0 + 20) * NS, "late"), _log((T0 + 10) * NS, "early")]))
        monkeypatch.setattr(a, "_sql", sql)
        rows = a.logs_search(LogFilter(before_ns=(T0 + 20) * NS), T0, T0 + 60, 50)
        assert [r["body"] for r in rows] == ["early"]

    def test_json_string_attributes_are_parsed(self, monkeypatch):
        a = _adapter()
        row = _log((T0 + 1) * NS, "x", attrs='{"hermes.session_id": "s1", "a": "b"}')
        monkeypatch.setattr(a, "_sql", _SQL((LOG_COLS, [row])))
        rows = a.logs_search(LogFilter(), T0, T0 + 60, 5)
        assert rows[0]["session_id"] == "s1" and rows[0]["attributes"] == {"a": "b"}

    def test_loggers(self, monkeypatch):
        a = _adapter()
        sql = _SQL((["scope_name", "n"], [["hermes_otel", 5], ["cli", 3], [None, 9]]))
        monkeypatch.setattr(a, "_sql", sql)
        assert a.loggers(T0, T0 + 60) == [
            {"logger": "hermes_otel", "count": 5},
            {"logger": "cli", "count": 3},
        ]
        assert 'GROUP BY "scope_name"' in sql.calls[0]


class TestSqlTransport:
    def test_posts_a_form_body_and_unwraps_records(self, monkeypatch):
        seen: Dict[str, Any] = {}

        def fake_execute(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = req.data.decode()
            seen["headers"] = dict(req.header_items())
            return {
                "output": [
                    {
                        "records": {
                            "schema": {"column_schemas": [{"name": "a"}, {"name": "b"}]},
                            "rows": [[1, "x"]],
                        }
                    }
                ]
            }

        monkeypatch.setattr(gp, "_execute", fake_execute)
        a = _adapter(user="u", password="p")
        cols, rows = a._sql("SELECT 1")
        assert cols == ["a", "b"] and rows == [[1, "x"]]
        assert seen["url"] == "http://localhost:4398/v1/sql?db=public"
        assert seen["body"] == "sql=SELECT+1"
        assert seen["headers"]["Authorization"] == "Basic dTpw"
        assert seen["headers"]["Content-type"] == "application/x-www-form-urlencoded"

    def test_server_error_is_a_backend_error(self, monkeypatch):
        monkeypatch.setattr(gp, "_execute", lambda req, timeout: {"error": "Table not found: x"})
        with pytest.raises(BackendError) as ei:
            _adapter()._sql("SELECT 1 FROM x")
        assert ei.value.kind == "backend" and "Table not found" in ei.value.detail

    def test_empty_output_is_no_rows(self, monkeypatch):
        monkeypatch.setattr(gp, "_execute", lambda req, timeout: {"output": [{"affectedrows": 0}]})
        assert _adapter()._sql("INSERT") == ([], [])
