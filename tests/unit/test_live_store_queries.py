"""Live store v2: indexed columns, server-side queries, retention, migration (#184)."""

from __future__ import annotations

import sqlite3
import time

import pytest

from hermes_otel.live_store import (
    SCHEMA_VERSION,
    LiveStore,
    span_kind,
    summarize_trace,
    trace_totals,
)

NOW = time.time_ns()


def _span(
    trace,
    name,
    sid,
    parent,
    attrs=None,
    start=NOW - 2_000_000_000,
    end=NOW - 1_000_000_000,
    status="OK",
):
    return {
        "trace_id": trace,
        "span_id": sid,
        "parent_span_id": parent,
        "name": name,
        "start_time_unix_nano": start,
        "end_time_unix_nano": end,
        "duration_ms": (end - start) / 1e6,
        "status": status,
        "attributes": attrs or {},
    }


@pytest.fixture()
def store(tmp_path):
    s = LiveStore(db_path=str(tmp_path / "live.db"))
    yield s
    s.close()


class TestSchema:
    def test_columns_are_extracted_and_indexed(self, store):
        store.add_span(_span("t1", "agent", "r", None, {"hermes.session_id": "s1"}, status="ERROR"))
        store.flush()  # rows are batched (#91); a raw connection sees committed rows only
        c = sqlite3.connect(store.db_path)
        row = c.execute(
            "SELECT trace_id, span_id, session_id, name, status, start_ns, end_ns FROM events"
        ).fetchone()
        assert row == ("t1", "r", "s1", "agent", "ERROR", NOW - 2_000_000_000, NOW - 1_000_000_000)
        assert int(c.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
        plan = c.execute(
            "EXPLAIN QUERY PLAN SELECT seq FROM events WHERE kind='span' AND session_id='s1'"
        ).fetchall()
        assert any("ix_events_kind_session" in str(p) for p in plan), plan
        plan = c.execute(
            "EXPLAIN QUERY PLAN SELECT seq FROM events WHERE kind='span' AND trace_id='t1'"
        ).fetchall()
        assert any("ix_events_kind_trace" in str(p) for p in plan), plan

    def test_v1_file_is_recreated(self, tmp_path):
        path = str(tmp_path / "old.db")
        c = sqlite3.connect(path)
        c.execute(
            "CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, ts INTEGER NOT NULL, data TEXT NOT NULL)"
        )
        c.execute("INSERT INTO events(kind, ts, data) VALUES('span', 1, '{}')")
        c.commit()
        c.close()
        s = LiveStore(db_path=path)
        try:
            assert s.stats()["spans"] == 0
            s.add_span(_span("t", "agent", "r", None))
            assert s.stats()["spans"] == 1 and s.cursor() == 1
        finally:
            s.close()

    def test_retention_by_age(self, tmp_path):
        s = LiveStore(db_path=str(tmp_path / "r.db"), retention_hours=1)
        try:
            old = NOW - 2 * 3600 * 1_000_000_000
            s.add_span(_span("old", "agent", "r0", None, start=old, end=old + 1))
            for i in range(64):  # trimming runs every 64 writes
                s.add_span(_span(f"t{i}", "agent", f"r{i}", None))
            assert s.query_traces(trace_id="old")["total"] == 0
            assert s.query_traces(trace_id="t3")["total"] == 1
        finally:
            s.close()


class TestHelpers:
    def test_trace_totals_counts_once(self):
        spans = [
            _span("t", "api.m", "a1", "l", {"gen_ai.usage.total_tokens": 10}),
            _span("t", "api.m", "a2", "l", {"gen_ai.usage.total_tokens": 20}),
            _span("t", "llm.m", "l", "r", {"gen_ai.usage.total_tokens": 30}),
            _span("t", "agent", "r", None, {"gen_ai.usage.total_tokens": 30}),
        ]
        assert trace_totals(spans) == {"tokens": 30.0, "cost": None}
        spans[-1]["attributes"] = {}
        assert trace_totals(spans)["tokens"] == 30.0  # api spans only, llm mirror ignored

    def test_summarize_trace(self):
        spans = [
            _span("t", "tool.terminal", "x", "r", status="ERROR"),
            _span(
                "t",
                "agent",
                "r",
                None,
                {"hermes.session_id": "s", "gen_ai.request.model": "m", "hermes.turn.number": 3},
            ),
        ]
        s = summarize_trace(spans)
        assert s["rootName"] == "agent" and s["rootKind"] == "agent" and s["error"] is True
        assert s["session"] == "s" and s["model"] == "m" and s["turn"] == 3 and s["spanCount"] == 2

    def test_span_kind(self):
        assert span_kind("api.gpt") == "api" and span_kind("cron:x") == "cron"
        assert span_kind("x", {"hermes.span_kind": "approval"}) == "approval"


class TestQueries:
    def test_traces_group_and_order(self, store):
        store.add_span(
            _span("t1", "agent", "r1", None, start=NOW - 9_000_000_000, end=NOW - 8_000_000_000)
        )
        store.add_span(_span("t2", "tool.x", "c2", "r2"))
        store.add_span(_span("t2", "agent", "r2", None))
        out = store.query_traces(limit=10)
        assert [t["traceId"] for t in out["traces"]] == ["t2", "t1"] and out["total"] == 2
        assert out["traces"][0]["spanCount"] == 2

    def test_metric_buckets_group_by(self, store):
        base = NOW - 100_000_000_000
        base -= base % 60_000_000_000  # bucket-aligned so the four points land in two buckets
        for i in range(4):
            store.add_metric(
                "token_usage",
                10,
                {"token_type": "input" if i < 2 else "output"},
                base + i * 30_000_000_000,
            )
        out = store.metric_buckets(
            "token_usage", base, base + 120_000_000_000, 60, group_by="token_type"
        )
        assert out["series"]["input"] == [20.0, None, None] and out["series"]["output"] == [
            None,
            20.0,
            None,
        ]
        assert store.metric_names() == [
            {"name": "token_usage", "count": 4, "lastTs": base + 90_000_000_000}
        ]

    def test_logs_and_loggers(self, store):
        store.add_log(
            {
                "level": "INFO",
                "logger": "a",
                "body": "one",
                "time_unix_nano": NOW - 3,
                "trace_id": "t",
            }
        )
        store.add_log(
            {
                "level": "WARNING",
                "logger": "b",
                "body": "two",
                "time_unix_nano": NOW - 2,
                "trace_id": "t",
                "session_id": "s",
            }
        )
        assert [l["body"] for l in store.query_logs(trace_id="t")] == ["two", "one"]
        assert [l["body"] for l in store.query_logs(level_min=30)] == ["two"]
        assert [l["body"] for l in store.query_logs(session="s")] == ["two"]
        assert [x["logger"] for x in store.loggers()] == ["a", "b"] or [
            x["logger"] for x in store.loggers()
        ] == ["b", "a"]


class TestLogAttribution:
    """Log lines are attributed to the single active session via the tracker (#186)."""

    def _root(self, trace_id: int):
        class Ctx:
            pass

        class Root:
            def get_span_context(self):
                c = Ctx()
                c.trace_id = trace_id
                return c

        return Root()

    def test_one_active_session_attributes_the_line(self, store):
        import logging

        from hermes_otel.span_tracker import SpanTracker
        from hermes_otel.tracer import _LiveLogHandler

        tracker = SpanTracker()
        tracker.push_parent(self._root(0xABC), session_id="sess-1")
        h = _LiveLogHandler(store, tracker=tracker)
        h.emit(logging.LogRecord("agent.loop", logging.INFO, __file__, 1, "hello", None, None))
        (rec,) = store.logs()
        assert rec["session_id"] == "sess-1"
        assert rec["trace_id"] == format(0xABC, "032x")
        assert [l["body"] for l in store.query_logs(session="sess-1")] == ["hello"]

    def test_two_active_sessions_stay_unattributed(self, store):
        import logging

        from hermes_otel.span_tracker import SpanTracker
        from hermes_otel.tracer import _LiveLogHandler

        tracker = SpanTracker()
        tracker.push_parent(self._root(1), session_id="a")
        tracker.push_parent(self._root(2), session_id="b")
        h = _LiveLogHandler(store, tracker=tracker)
        h.emit(logging.LogRecord("x", logging.INFO, __file__, 1, "ambiguous", None, None))
        (rec,) = store.logs()
        assert rec["session_id"] is None and rec["trace_id"] is None
        assert tracker.single_active_session() is None
        tracker.pop_parent(session_id="b")
        assert tracker.single_active_session()[0] == "a"
