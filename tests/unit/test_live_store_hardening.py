"""Live store hardening (#291): ok means no errors, partial traces, exact-ish
model filter, LIKE escaping, kinds, sessions paging, flush re-queue, corrupt
file, in-place migration, host-sample cap, trim on open.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from hermes_otel import live_store as ls
from hermes_otel.live_store import (
    HOST_SAMPLE_MAX_ROWS,
    SCHEMA_VERSION,
    LiveStore,
    root_of,
    summarize_trace,
    trace_totals,
)

NOW = time.time_ns()


def _span(trace, name, sid, parent, attrs=None, start=None, end=None, status="OK"):
    start = NOW - 2_000_000_000 if start is None else start
    end = NOW - 1_000_000_000 if end is None else end
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


class TestStatusAndPartial:
    def test_ok_means_no_error_span_in_the_trace(self, store):
        store.add_span(_span("bad", "tool.terminal", "c", "r", status="ERROR"))
        store.add_span(_span("bad", "agent", "r", None))
        store.add_span(_span("good", "agent", "r2", None))
        assert [t["traceId"] for t in store.query_traces(status="ok")["traces"]] == ["good"]
        assert [t["traceId"] for t in store.query_traces(status="error")["traces"]] == ["bad"]

    def test_in_flight_trace_is_partial_not_a_child(self, store):
        early = _span(
            "t", "api.m", "a1", "l", {"gen_ai.usage.total_tokens": 10}, NOW - 9_000_000_000
        )
        late = _span("t", "api.m", "a2", "l", {"gen_ai.usage.total_tokens": 20})
        tool = _span("t", "tool.x", "c", "l", start=NOW - 5_000_000_000)
        store.add_span(late)
        store.add_span(tool)
        store.add_span(early)
        (row,) = store.query_traces()["traces"]
        assert row["partial"] is True
        assert row["rootName"] == "api.m" and row["tokens"] == 30  # api sum, not one call
        root, partial = root_of([late, tool, early])
        assert partial and root is early
        assert trace_totals([late, early])["tokens"] == 30
        store.add_span(_span("t", "agent", "l", None, {"gen_ai.usage.total_tokens": 30}))
        (row,) = store.query_traces()["traces"]
        assert row["partial"] is False and row["rootName"] == "agent"
        assert store.trace("t")["trace"]["partial"] is False

    def test_summarize_single_orphan(self):
        s = summarize_trace([_span("t", "tool.x", "c", "missing")])
        assert s["partial"] is True and s["rootName"] == "tool.x"


class TestFilters:
    def test_model_matches_the_attribute_not_the_prompt(self, store):
        store.add_span(
            _span(
                "a",
                "agent",
                "r",
                None,
                {"gen_ai.request.model": "claude-x", "output.value": "we talked about gpt-4"},
            )
        )
        store.add_span(_span("b", "agent", "r2", None, {"llm.model_name": "openai/gpt-4o"}))
        assert [t["traceId"] for t in store.query_traces(model="gpt-4")["traces"]] == ["b"]
        assert store.query_traces(model="claude")["total"] == 1

    def test_like_wildcards_are_literal(self, store):
        store.add_span(_span("a", "tool.read_file", "r", None, {"input.value": "100% done"}))
        store.add_span(_span("b", "tool.readXfile", "r2", None, {"input.value": "plain"}))
        assert store.query_traces(name="read_file")["total"] == 1
        assert store.query_traces(text="100%")["total"] == 1
        assert store.query_traces(text="%")["total"] == 1
        store.add_log({"body": "50% there", "time_unix_nano": NOW})
        store.add_log({"body": "nothing", "time_unix_nano": NOW})
        assert len(store.query_logs(text="%")) == 1

    def test_kind_other_and_attribute_kinds(self, store):
        store.add_span(_span("a", "mcp.ping", "r", None))
        store.add_span(_span("b", "x", "r2", None, {"hermes.span_kind": "skill"}))
        store.add_span(_span("c", "approval.rm", "r3", None))
        store.add_span(_span("d", "tool.x", "r4", None))
        assert [t["traceId"] for t in store.query_traces(kind="other")["traces"]] == ["a"]
        assert [t["traceId"] for t in store.query_traces(kind="skill")["traces"]] == ["b"]
        assert [t["traceId"] for t in store.query_traces(kind="approval")["traces"]] == ["c"]
        assert store.query_traces(kind="tool")["total"] == 1

    def test_offset_past_the_end_and_has_more(self, store):
        for i in range(3):
            store.add_span(_span(f"t{i}", "agent", f"r{i}", None, start=NOW - (i + 2) * 10**9))
        page = store.query_traces(limit=2)
        assert len(page["traces"]) == 2 and page["has_more"] is True
        last = store.query_traces(limit=2, offset=2)
        assert len(last["traces"]) == 1 and last["has_more"] is False
        assert store.query_traces(limit=2, offset=99) == {
            "traces": [],
            "total": 3,
            "has_more": False,
            "next_before_ns": None,
        }

    def test_keyset_cursor_survives_a_newer_trace_between_pages(self, store):
        for i in (3, 2, 1):  # t3 oldest … t1 newest
            store.add_span(_span(f"t{i}", "agent", f"r{i}", None, start=NOW - i * 10**10))
        page1 = store.query_traces(limit=1)
        assert [t["traceId"] for t in page1["traces"]] == ["t1"] and page1["has_more"]
        assert page1["next_before_ns"] == NOW - 10**10
        # A newer turn lands between page 1 and page 2: an offset page would
        # show t1 again; the cursor page does not.
        store.add_span(_span("t0", "agent", "r0", None, start=NOW - 5 * 10**9))
        page2 = store.query_traces(limit=1, before_ns=page1["next_before_ns"])
        assert [t["traceId"] for t in page2["traces"]] == ["t2"] and page2["has_more"]
        assert page2["total"] == 4  # total ignores the cursor
        page3 = store.query_traces(limit=1, before_ns=page2["next_before_ns"])
        assert [t["traceId"] for t in page3["traces"]] == ["t3"] and not page3["has_more"]
        page4 = store.query_traces(limit=1, before_ns=page3["next_before_ns"])
        assert page4["traces"] == [] and page4["next_before_ns"] is None
        # Nothing skipped, nothing repeated across the three pages.
        seen = [t["traceId"] for p in (page1, page2, page3) for t in p["traces"]]
        assert seen == ["t1", "t2", "t3"]


class TestSessions:
    def test_every_session_counted_newest_first_with_has_more(self, store):
        for i in range(5):
            store.add_span(
                _span(
                    f"t{i}",
                    "agent",
                    f"r{i}",
                    None,
                    {"hermes.session_id": f"s{i}"},
                    start=NOW - (i + 2) * 10**9,
                    end=NOW - (i + 1) * 10**9,
                )
            )
        out = store.sessions(limit=2)
        assert [s["session"] for s in out["sessions"]] == ["s0", "s1"] and out["has_more"]
        assert store.sessions(limit=10)["has_more"] is False


class TestBuckets:
    def test_every_aggregation_and_missing_group_attribute(self, store):
        base = NOW - 10 * 60 * 10**9
        base -= base % (60 * 10**9)
        for v, attrs in ((1, {"m": "a"}), (3, {"m": "a"}), (5, {})):
            store.add_metric("x", v, attrs, base + 1)
        for agg, want in (("sum", 9.0), ("count", 3.0), ("avg", 3.0), ("max", 5.0), ("last", 5.0)):
            out = store.metric_buckets("x", base, base + 60 * 10**9, 60, agg=agg)
            assert out["series"]["_"][0] == want, agg
        grouped = store.metric_buckets("x", base, base + 60 * 10**9, 60, group_by="m")
        assert set(grouped["series"]) == {"a", "—"}


class TestDurability:
    def test_commit_failure_holds_the_batch(self, tmp_path):
        path = str(tmp_path / "locked.db")
        s = LiveStore(db_path=path)
        try:
            s._stop.set()  # flush on this thread only (no writer thread, no 3 s wait)
            s._conn().execute("PRAGMA busy_timeout=50")
            other = sqlite3.connect(path)
            other.execute("BEGIN EXCLUSIVE")
            s.add_span(_span("t", "agent", "r", None))
            assert s.flush() == 0
            assert s.stats()["pending"] == 1 and "locked" in s.stats()["write_error"].lower()
            other.rollback()
            other.close()
            assert s.flush() == 1
            st = s.stats()
            assert st["spans"] == 1 and "pending" not in st and "write_error" not in st
        finally:
            s.close()

    def test_held_rows_are_bounded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ls, "MAX_PENDING_ROWS", 10)
        path = str(tmp_path / "locked.db")
        s = LiveStore(db_path=path)
        try:
            s._stop.set()
            s._conn().execute("PRAGMA busy_timeout=20")
            other = sqlite3.connect(path)
            other.execute("BEGIN EXCLUSIVE")
            for i in range(30):
                s.add_span(_span("t", str(i), f"r{i}", None))
                s.flush()
            assert s.stats()["pending"] <= 10
            other.rollback()
            other.close()
            s.flush()
            assert [x["name"] for x in s.spans(limit=1)] == ["29"]  # the newest survived
        finally:
            s.close()

    def test_corrupt_file_is_reported_not_hidden(self, tmp_path):
        path = tmp_path / "corrupt.db"
        path.write_bytes(b"this is not a sqlite file" * 100)
        s = LiveStore(db_path=str(path))
        try:
            st = s.stats()
            assert st["spans"] == 0 and "open_error" in st
            assert "DatabaseError" in st["open_error"] or "not a database" in st["open_error"]
            s.add_span(_span("t", "agent", "r", None))  # never raises
            s.flush()
        finally:
            s.close()


class TestMigration:
    def _v2_file(self, path, version=2):
        c = sqlite3.connect(path)
        c.execute(
            "CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,"
            " ts INTEGER NOT NULL, data TEXT NOT NULL, trace_id TEXT, span_id TEXT,"
            " parent_span_id TEXT, session_id TEXT, name TEXT, status TEXT, start_ns INTEGER,"
            " end_ns INTEGER, duration_ms REAL, level INTEGER, logger TEXT, value REAL)"
        )
        c.execute(
            "INSERT INTO events(kind, ts, data, trace_id, span_id, name, status, start_ns)"
            " VALUES('span', ?, ?, 't', 'r', 'agent', 'OK', ?)",
            (NOW, '{"trace_id": "t", "span_id": "r", "name": "agent", "attributes": {}}', NOW),
        )
        c.execute(f"PRAGMA user_version={version}")
        c.commit()
        c.close()

    def test_v2_is_kept_and_stamped_v3(self, tmp_path):
        path = str(tmp_path / "v2.db")
        self._v2_file(path)
        s = LiveStore(db_path=path)
        try:
            assert s.stats()["spans"] == 1 and s.query_traces()["total"] == 1
            assert int(sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0]) == 3
        finally:
            s.close()

    def test_newer_file_is_read_but_never_restamped(self, tmp_path):
        path = str(tmp_path / "v9.db")
        self._v2_file(path, version=SCHEMA_VERSION + 6)
        s = LiveStore(db_path=path)
        try:
            assert s.stats()["spans"] == 1
            assert (
                int(sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0])
                == SCHEMA_VERSION + 6
            )
        finally:
            s.close()


class TestBounds:
    def test_trim_on_open_bounds_a_file_nobody_trimmed(self, tmp_path):
        path = str(tmp_path / "grow.db")
        w = LiveStore(db_path=path, max_rows=1000)
        for i in range(30):  # fewer than 64: never trimmed by the writer
            w.add_span(_span(f"t{i}", "agent", f"r{i}", None))
        w.close()
        r = LiveStore(db_path=path, max_rows=10)
        try:
            assert r.stats()["spans"] == 10
            assert r.spans(limit=1)[0]["name"] == "agent" and r.query_traces()["total"] == 10
        finally:
            r.close()

    def test_host_samples_cannot_evict_turn_metrics(self, tmp_path):
        s = LiveStore(db_path=str(tmp_path / "h.db"), max_rows=50)
        try:
            for i in range(5):
                s.add_metric("hermes.token.usage", 10, {"token_type": "input"}, NOW - i)
            for i in range(400):
                s.add_metric("process.cpu.utilization", 0.5, {}, NOW + i)
                s.add_metric("hw.gpu.utilization", 0.2, {}, NOW + i)
            s.flush()
            names = {n["name"]: n["count"] for n in s.metric_names()}
            assert names["hermes.token.usage"] == 5
            assert names["process.cpu.utilization"] + names["hw.gpu.utilization"] <= (
                HOST_SAMPLE_MAX_ROWS + 2 * 64
            )
        finally:
            s.close()

    def test_reader_uses_the_configured_limits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ls, "_LIVE_STORE", None)
        monkeypatch.setattr(ls, "_STORES_BY_PATH", {})
        monkeypatch.setenv("HERMES_OTEL_LIVE_DB", str(tmp_path / "cfg.db"))
        monkeypatch.setattr(
            ls,
            "_configured_limits",
            lambda: {"max_rows": 123, "retention_hours": 0},
        )
        st = ls.get_live_store_for_home()
        try:
            assert st is not None and st.max_rows == 123 and st.retention_ns == 0
        finally:
            st.close()

    def test_configured_limits_read_the_plugin_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_OTEL_DASHBOARD_LIVE_MAX_SPANS", "77")
        monkeypatch.setenv("HERMES_OTEL_DASHBOARD_LIVE_RETENTION_HOURS", "2")
        assert ls._configured_limits() == {"max_rows": 77, "retention_hours": 2.0}
