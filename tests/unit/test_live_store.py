"""Unit tests for the SQLite-backed live store (zero-config dashboard)."""

import sqlite3
import threading
import time

from hermes_otel.live_store import LiveStore
from hermes_otel.live_store_registry import get_live_store


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
        s.close()

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
        try:
            writer.add_span({"name": "from-gateway"})
            writer.flush()  # the writer batches (#91); the reader sees committed rows
            assert [s["name"] for s in reader.spans()] == ["from-gateway"]
            assert reader.cursor() == writer.cursor() == 1
        finally:
            writer.close()
            reader.close()

    def test_close_is_terminal_and_old_references_cannot_reopen(self, tmp_path):
        store = LiveStore(db_path=str(tmp_path / "terminal.db"))
        store.add_log({"body": "before close", "level": "INFO", "logger": "test"})
        store.flush()
        store.close()

        # A late log processor callback or dashboard read may still hold this
        # object. Once closed, neither path may reopen SQLite on the old store.
        assert store.logs() == []
        assert store.flush() == 0
        assert store._conns == []

    def test_failed_close_keeps_live_writer_tracked(self, monkeypatch, tmp_path):
        store = LiveStore(db_path=str(tmp_path / "writer-timeout.db"))
        store._pending.append(("span", 1, "{}", (), ()))
        release_writer = threading.Event()
        writer = threading.Thread(target=release_writer.wait)
        writer.start()
        store._writer = writer
        original_flush_pending = store._flush_pending

        # Simulate join(timeout) returning while the writer is still alive and
        # a terminal flush failure retaining the pending row for retry.
        monkeypatch.setattr(writer, "join", lambda timeout=None: None)
        monkeypatch.setattr(store, "_flush_pending", lambda *, allow_closed=False: 0)
        try:
            assert store.close() is False
            assert writer.is_alive()
            assert store._writer is writer

            # Keeping the reference prevents _ensure_writer from launching a
            # replacement while the timed-out writer can still resume.
            store._ensure_writer()
            assert store._writer is writer
        finally:
            release_writer.set()
            threading.Thread.join(writer, timeout=2.0)
            current_writer = store._writer
            if current_writer is not None and current_writer is not writer:
                store._stop.set()
                store._wake.set()
                current_writer.join(timeout=2.0)
            store._writer = None
            store._pending.clear()
            monkeypatch.setattr(store, "_flush_pending", original_flush_pending)
            assert store.close() is True

    def test_close_racing_with_flush_cannot_reopen(self, monkeypatch, tmp_path):
        store = LiveStore(db_path=str(tmp_path / "race.db"))
        store.add_span({"name": "race"})
        store._stop.set()
        store._wake.set()
        if store._writer is not None:
            store._writer.join(timeout=2.0)
        store._writer = None
        entered = threading.Event()
        proceed = threading.Event()
        original_flush_pending = store._flush_pending

        def block_public_flush(*, allow_closed=False):
            if not allow_closed:
                entered.set()
                assert proceed.wait(timeout=2.0)
            return original_flush_pending(allow_closed=allow_closed)

        monkeypatch.setattr(store, "_flush_pending", block_public_flush)
        flush_thread = threading.Thread(target=store.flush)
        flush_thread.start()
        assert entered.wait(timeout=2.0)

        close_thread = threading.Thread(target=store.close)
        close_thread.start()
        deadline = time.monotonic() + 2.0
        while not store._closed and time.monotonic() < deadline:
            time.sleep(0.01)
        assert store._closed is True
        close_thread.join(timeout=2.0)
        assert not close_thread.is_alive()
        # Simulate a row requeued by a failed close flush. A racing public
        # flush must reject it without trying to reopen the closed store.
        store._pending.append(("span", 2, "{}", (), ()))
        proceed.set()
        flush_thread.join(timeout=2.0)

        assert not flush_thread.is_alive()
        assert store.last_write_error is None
        assert store._conns == []

    def test_close_racing_with_connection_registration_closes_late_connection(
        self, monkeypatch, tmp_path
    ):
        import hermes_otel.live_store as live_store_module

        store = LiveStore(db_path=str(tmp_path / "late-connection.db"))
        store._stop.set()  # keep this repro on the calling thread
        store.add_span({"name": "already-committed"})
        assert store.flush() == 1

        created = threading.Event()
        proceed = threading.Event()
        created_conn = None
        real_connect = live_store_module.sqlite3.connect

        def gated_connect(*args, **kwargs):
            nonlocal created_conn
            created_conn = real_connect(*args, **kwargs)
            created.set()
            assert proceed.wait(timeout=2.0)
            return created_conn

        monkeypatch.setattr(live_store_module.sqlite3, "connect", gated_connect)
        result = {}

        reader = threading.Thread(target=lambda: result.setdefault("rows", store.spans()))
        reader.start()
        assert created.wait(timeout=2.0)

        closer = threading.Thread(target=store.close)
        closer.start()
        closer.join(timeout=2.0)
        assert not closer.is_alive()

        proceed.set()
        reader.join(timeout=2.0)
        assert not reader.is_alive()
        assert result["rows"] == []
        assert store._conns == []
        assert created_conn is not None
        try:
            created_conn.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            pass
        else:
            raise AssertionError("connection registered after terminal close remained open")

    def test_release_live_store_closes_and_forgets_matching_registry_entries(self, monkeypatch):
        import hermes_otel.live_store_registry as registry

        class Store:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        store = Store()
        other = Store()
        monkeypatch.setattr(registry, "_LIVE_STORE", store)
        monkeypatch.setattr(registry, "_STORES_BY_PATH", {"live.db": store, "other.db": other})

        registry.release_live_store(store)

        assert store.closed is True
        assert registry._LIVE_STORE is None
        assert registry._STORES_BY_PATH == {"other.db": other}

    def test_release_live_store_keeps_failed_close_registered(self, monkeypatch):
        import hermes_otel.live_store_registry as registry

        class Store:
            def close(self):
                return False

        store = Store()
        monkeypatch.setattr(registry, "_LIVE_STORE", store)
        monkeypatch.setattr(registry, "_STORES_BY_PATH", {"live.db": store})

        assert registry.release_live_store(store) is False
        assert registry._LIVE_STORE is store
        assert registry._STORES_BY_PATH == {"live.db": store}

    def test_flush_after_close_does_not_touch_connection(self, monkeypatch, tmp_path):
        store = LiveStore(db_path=str(tmp_path / "closed-flush.db"))
        store.close()
        store._pending = [("span", 1, "{}", (), ())]

        def fail_if_called(*args, **kwargs):
            raise AssertionError("closed public flush attempted to open SQLite")

        monkeypatch.setattr(store, "_conn", fail_if_called)
        assert store.flush() == 0
        assert store.last_write_error is None
        assert store._conns == []

    def test_live_log_processor_feeds_store(self, store):
        import logging

        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk.resources import Resource

        from hermes_otel.log_handler import LiveLogProcessor

        provider = LoggerProvider(resource=Resource.create({}))
        provider.add_log_record_processor(LiveLogProcessor(store))
        handler = LoggingHandler(level=logging.DEBUG, logger_provider=provider)
        lg = logging.getLogger("my.logger")
        lg.propagate = False
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)
        try:
            lg.warning("boom %s", "x")
            lg.debug("below the live floor")
        finally:
            lg.removeHandler(handler)
        (row,) = store.logs()
        assert row["level"] == "WARN"  # OTel spelling since store v3 (#266)
        assert row["severity_number"] == 13
        assert row["logger"] == "my.logger" and row["scope"] == "my.logger"
        assert row["body"] == "boom x"
        assert row["span_id"] is None and row["event_name"] is None
        assert isinstance(row["attributes"], dict) and "code.function.name" in row["attributes"]

    def test_live_log_noise(self):
        from hermes_otel.log_handler import _is_live_noise

        # dropped: noisy logger + probe substrings
        assert _is_live_noise("gateway.config", "anything") is True
        assert (
            _is_live_noise("gateway.run", "kanban notifier: board default has no subscriptions")
            is True
        )
        assert _is_live_noise("x", "Plugin platform 'raft' available but not configured") is True
        # kept: real agent activity, and a non-string body
        assert _is_live_noise("hermes_otel", "exported 3 spans") is False
        assert _is_live_noise("hermes_otel", {"k": "v"}) is False

    def test_singleton_create_flag(self, tmp_path):
        import hermes_otel.live_store_registry as ls

        ls._LIVE_STORE = None
        assert get_live_store(create=False) is None
        a = get_live_store(create=True, db_path=str(tmp_path / "s.db"))
        b = get_live_store(create=False)
        assert a is b is not None
        ls._LIVE_STORE = None  # cleanup
