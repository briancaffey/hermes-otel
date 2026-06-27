"""Unit tests for the SQLite-backed live store (zero-config dashboard)."""

import pytest
from hermes_otel.live_store import LiveStore, get_live_store


@pytest.fixture()
def store(tmp_path):
    return LiveStore(db_path=str(tmp_path / "live.db"))


class TestLiveStore:
    def test_monotonic_cursor_and_since(self, store):
        store.add_span({"name": "a"})
        store.add_metric("m", 1, {}, 0)
        store.add_span({"name": "b"})
        first_seq = store.spans()[0]["seq"]
        later = store.spans(since=first_seq)
        assert [sp["name"] for sp in later] == ["b"]
        assert store.cursor() == 3  # shared autoincrement across kinds

    def test_seq_shared_across_signals(self, store):
        store.add_span({"name": "a"})
        store.add_metric("m", 1, {}, 0)
        store.add_log({"body": "x"})
        assert store.spans()[0]["seq"] == 1
        assert store.metrics()[0]["seq"] == 2
        assert store.logs()[0]["seq"] == 3

    def test_limit_returns_most_recent(self, store):
        for i in range(10):
            store.add_span({"name": str(i)})
        got = store.spans(limit=3)
        assert [sp["name"] for sp in got] == ["7", "8", "9"]

    def test_bounded_trim(self, tmp_path):
        s = LiveStore(db_path=str(tmp_path / "b.db"), max_rows=50)
        for i in range(300):
            s.add_span({"name": str(i)})
        n = len(s.spans())
        # Trimming is periodic (every 64 writes), bounded by max_rows.
        assert 0 < n <= 50 + 64
        # And it kept the NEWEST ones.
        assert s.spans(limit=1)[0]["name"] == "299"

    def test_metric_shape(self, store):
        store.add_metric("hermes.cost.usage", 0.05, {"model": "gpt-4"}, 1234)
        m = store.metrics()[0]
        assert m["name"] == "hermes.cost.usage"
        assert m["value"] == 0.05
        assert m["attributes"] == {"model": "gpt-4"}
        assert m["time_unix_nano"] == 1234

    def test_stats_and_clear(self, store):
        store.add_span({"name": "a"})
        store.add_metric("m", 1, {}, 0)
        st = store.stats()
        assert st["spans"] == 1 and st["metrics"] == 1 and st["cursor"] == 2
        store.clear()
        assert store.stats() == {"spans": 0, "metrics": 0, "logs": 0, "cursor": 0}

    def test_cross_process_via_shared_file(self, tmp_path):
        # Two LiveStore objects on the same file = the gateway↔dashboard split.
        db = str(tmp_path / "shared.db")
        writer = LiveStore(db_path=db)
        reader = LiveStore(db_path=db)
        writer.add_span({"name": "from-gateway"})
        assert [s["name"] for s in reader.spans()] == ["from-gateway"]
        assert reader.cursor() == writer.cursor() == 1

    def test_live_log_handler_feeds_store(self, store):
        import logging

        from hermes_otel.tracer import _LiveLogHandler

        h = _LiveLogHandler(store)
        rec = logging.LogRecord("my.logger", logging.WARNING, __file__, 1, "boom %s", ("x",), None)
        h.emit(rec)
        logs = store.logs()
        assert len(logs) == 1
        assert logs[0]["level"] == "WARNING"
        assert logs[0]["logger"] == "my.logger"
        assert logs[0]["body"] == "boom x"

    def test_live_log_noise_filter(self):
        import logging

        from hermes_otel.tracer import _LiveLogNoiseFilter

        nf = _LiveLogNoiseFilter()

        def rec(name, msg):
            return logging.LogRecord(name, logging.INFO, __file__, 1, msg, None, None)

        # dropped: noisy logger + probe substrings
        assert nf.filter(rec("gateway.config", "anything")) is False
        assert (
            nf.filter(rec("gateway.run", "kanban notifier: board default has no subscriptions"))
            is False
        )
        assert nf.filter(rec("x", "Plugin platform 'raft' available but not configured")) is False
        # kept: real agent activity
        assert nf.filter(rec("hermes_otel", "exported 3 spans")) is True

    def test_singleton_create_flag(self, tmp_path):
        import hermes_otel.live_store as ls

        ls._LIVE_STORE = None
        assert get_live_store(create=False) is None
        a = get_live_store(create=True, db_path=str(tmp_path / "s.db"))
        b = get_live_store(create=False)
        assert a is b is not None
        ls._LIVE_STORE = None  # cleanup
