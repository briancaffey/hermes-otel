"""Phase 4 (#268): richer log rows from every adapter, event filters on the routes,
and the live store's events-only / event-name filters."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_otel.dashboard import backends, plugin_api
from hermes_otel.dashboard.backends import _loki, base
from hermes_otel.dashboard.backends.base import LogFilter, finish_log_row
from hermes_otel.live_store import LiveStore


class TestRowShape:
    def test_finish_log_row_normalises_level_and_collects_extras(self):
        row = finish_log_row(
            {"level": "warning", "logger": "x", "body": "b", "time_unix_nano": 1},
            {
                "hermes.session_id": "s",
                "code.function.name": "f",
                "empty": "",
                "none": None,
                "body": "ignored",
            },
        )
        assert row["level"] == "WARN" and row["severity_number"] == 13
        assert row["attributes"] == {"hermes.session_id": "s", "code.function.name": "f"}
        assert row["event_name"] is None and row["span_id"] is None

    def test_event_name_is_lifted_out_of_attributes(self):
        row = finish_log_row(
            {"level": "INFO", "attributes": {"event_name": "hermes.tool.call", "a": 1}}
        )
        assert row["event_name"] == "hermes.tool.call" and row["attributes"] == {"a": 1}

    def test_severity_numbers_accept_python_and_otel_spellings(self):
        assert base.severity_number_for("CRITICAL") == 21 == base.severity_number_for("FATAL")
        assert base.severity_number_for("30") == 13 and base.severity_number_for("13") == 13
        assert base.severity_number_for("nope") is None

    def test_loki_record_keeps_structured_metadata_as_attributes(self):
        row = _loki._record(
            {
                "severity_text": "ERROR",
                "severity_number": "17",
                "scope_name": "agent.loop",
                "trace_id": "t" * 32,
                "span_id": "s" * 16,
                "hermes_session_id": "sess",
                "event_name": "hermes.api.error",
                "hermes_log_attribution": "context",
                "service_name": "hermes-agent",
            },
            "1700000000000000000",
            "boom",
        )
        assert row["level"] == "ERROR" and row["severity_number"] == 17
        assert row["span_id"] == "s" * 16 and row["event_name"] == "hermes.api.error"
        # Loki labels are underscored; the row gives the attribute its dotted name back.
        assert row["attributes"] == {"hermes.log.attribution": "context"}

    def test_loki_logql_carries_the_event_filters(self):
        assert '| event_name="hermes.tool.call"' in _loki.logql_for(
            LogFilter(event_name="hermes.tool.call")
        )
        assert '| event_name!=""' in _loki.logql_for(LogFilter(events_only=True))
        assert "event_name" not in _loki.logql_for(LogFilter())


class TestLiveStoreEventFilters:
    @pytest.fixture()
    def store(self, tmp_path):
        s = LiveStore(db_path=str(tmp_path / "live.db"))
        s.add_log({"level": "INFO", "logger": "a", "body": "plain", "time_unix_nano": 10})
        s.add_log(
            {
                "level": "INFO",
                "logger": "hermes_otel",
                "body": "tool",
                "time_unix_nano": 20,
                "event_name": "hermes.tool.call",
                "attributes": {"gen_ai.tool.name": "bash"},
            }
        )
        s.add_log(
            {
                "level": "WARN",
                "logger": "hermes_otel",
                "body": "turn",
                "time_unix_nano": 30,
                "event_name": "hermes.turn.end",
            }
        )
        s.flush()
        yield s
        s.close()

    def test_events_only_and_event_name(self, store):
        assert [r["body"] for r in store.query_logs(events_only=True)] == ["turn", "tool"]
        assert [r["body"] for r in store.query_logs(event_name="hermes.tool.call")] == ["tool"]
        assert [r["body"] for r in store.query_logs()] == ["turn", "tool", "plain"]
        (row,) = store.query_logs(event_name="hermes.tool.call")
        assert row["attributes"] == {"gen_ai.tool.name": "bash"}


class TestRoutes:
    @pytest.fixture()
    def client(self, monkeypatch, tmp_path):
        store = LiveStore(db_path=str(tmp_path / "live.db"))
        store.add_log(
            {
                "level": "INFO",
                "logger": "a",
                "body": "plain",
                "time_unix_nano": 1_700_000_000_000_000_000,
            }
        )
        store.add_log(
            {
                "level": "INFO",
                "logger": "hermes_otel",
                "body": "ev",
                "time_unix_nano": 1_700_000_000_500_000_000,
                "event_name": "hermes.tool.call",
            }
        )
        store.add_log(
            {
                "level": "INFO",
                "logger": "a",
                "body": "later",
                "time_unix_nano": 1_700_000_100_000_000_000,
            }
        )
        store.flush()
        monkeypatch.setattr(plugin_api, "_get_live_store", lambda: store)
        app = FastAPI()
        app.include_router(plugin_api.router)
        with TestClient(app) as c:
            yield c
        store.close()

    def test_live_search_event_and_window_params(self, client):
        r = client.get(
            "/live/logs/search", params={"start_s": 1_699_999_000, "events_only": "true"}
        )
        assert [x["body"] for x in r.json()["logs"]] == ["ev"]
        r = client.get(
            "/live/logs/search", params={"start_s": 1_699_999_000, "event_name": "hermes.tool.call"}
        )
        assert [x["body"] for x in r.json()["logs"]] == ["ev"]
        # a ±30 s window around the first line excludes the one 100 s later
        r = client.get(
            "/live/logs/search", params={"start_s": 1_699_999_970, "end_s": 1_700_000_031}
        )
        assert [x["body"] for x in r.json()["logs"]] == ["ev", "plain"]

    def test_backend_search_passes_event_and_window_to_the_adapter(self, monkeypatch):
        seen = {}

        class FakeAdapter:
            cfg = {"type": "openobserve", "name": "oo"}
            supports_logs = True

            def logs_search(self, f, start_s, end_s, limit):
                seen.update(f=f, start_s=start_s, end_s=end_s, limit=limit)
                return []

        monkeypatch.setattr(plugin_api, "_adapter_for", lambda backend, need: FakeAdapter())
        app = FastAPI()
        app.include_router(plugin_api.router)
        with TestClient(app) as c:
            r = c.get(
                "/logs/search",
                params={
                    "backend": "oo",
                    "events_only": "true",
                    "event_name": "hermes.turn.end",
                    "start_s": 100,
                    "end_s": 200,
                    "limit": 10,
                },
            )
        assert r.status_code == 200
        assert seen["f"].events_only is True and seen["f"].event_name == "hermes.turn.end"
        assert (seen["start_s"], seen["end_s"]) == (100, 200)


class TestOpenObserveEventColumn:
    def test_missing_event_name_column_means_no_events_not_an_error(self):
        from fastapi import HTTPException

        from hermes_otel.dashboard.backends.openobserve import OpenObserveAdapter

        a = OpenObserveAdapter(
            {
                "type": "openobserve",
                "name": "oo",
                "endpoint": "http://localhost:5080/api/default/v1/traces",
            }
        )
        err = HTTPException(
            status_code=502,
            detail='Backend returned 400: {"code":20004,"message":"Search field not found: Schema error: No field named event_name. Valid fields are default._timestamp"}',
        )
        with patch.object(a, "_search", side_effect=err):
            assert a.logs_search(LogFilter(events_only=True), 0, 10, 50) == []
            assert a.logs_search(LogFilter(event_name="hermes.tool.call"), 0, 10, 50) == []
            with pytest.raises(HTTPException):
                a.logs_search(LogFilter(), 0, 10, 50)  # a plain query still surfaces the error
        # the wording current builds use (seen live 2026-10-09, #299)
        err2 = HTTPException(
            status_code=502,
            detail='Backend returned 400: {"code":20004,"message":"unknown field \'event_name\'","hint":"no similar field found; use the stream schema endpoint to list fields"}',
        )
        with patch.object(a, "_search", side_effect=err2):
            assert a.logs_search(LogFilter(events_only=True), 0, 10, 50) == []
            with pytest.raises(HTTPException):
                a.logs_search(LogFilter(), 0, 10, 50)
        # an unrelated 400 on an events query is still an error
        err3 = HTTPException(status_code=502, detail="Backend returned 400: unknown field 'foo'")
        with patch.object(a, "_search", side_effect=err3):
            with pytest.raises(HTTPException):
                a.logs_search(LogFilter(events_only=True), 0, 10, 50)
