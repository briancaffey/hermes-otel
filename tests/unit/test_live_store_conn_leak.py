"""A LiveStore read/written from many short-lived threads must not keep one
SQLite connection (2 fds: db + wal) per dead thread for the life of the process.

The gateway serves dashboard reads and hook writes from transient worker
threads; before this fix every such thread leaked a connection, the gateway
crossed FD_SETSIZE (1024), and select()-based pipe drains elsewhere in the
process started raising ValueError -> empty terminal output.
"""

import threading

from hermes_otel.live_store import LiveStore


def _touch_from_new_thread(store: LiveStore) -> None:
    t = threading.Thread(target=lambda: store.spans(limit=1))
    t.start()
    t.join()


def test_dead_thread_connections_are_reaped(tmp_path):
    store = LiveStore(db_path=str(tmp_path / "live.db"))
    try:
        for _ in range(50):
            _touch_from_new_thread(store)
        # One for the constructor thread (still alive) + at most one dead
        # thread's not-yet-reaped connection; never one per dead thread.
        assert len(store._conns) <= 2
    finally:
        store.close()


def test_live_thread_connection_survives_reap(tmp_path):
    store = LiveStore(db_path=str(tmp_path / "live.db"))
    try:
        store.add_span({"name": "a"})
        mine = store._conn()
        for _ in range(5):
            _touch_from_new_thread(store)
        assert store._conn() is mine
        mine.execute("SELECT 1").fetchone()  # still open
        assert [s["name"] for s in store.spans()] == ["a"]
    finally:
        store.close()
