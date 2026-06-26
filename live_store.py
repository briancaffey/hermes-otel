"""Shared, bounded telemetry store for the zero-config dashboard.

The plugin's hooks run in the **gateway** process; the dashboard is served by a
**separate** ``hermes dashboard`` process. They don't share memory, so the live
store is backed by a small **SQLite** file (WAL mode) that both processes open —
the gateway writes recent spans / metrics / logs, the dashboard reads them. This
mirrors how Hermes' own ``kanban`` plugin streams cross-process via an
append-only SQLite ``events`` table.

Design:
- One append-only ``events`` table: ``seq`` (autoincrement = the cursor), a
  ``kind`` discriminator (span/metric/log), and a JSON ``data`` blob.
- Bounded: trimmed to the last ``max_rows`` rows (cheap, periodic).
- WAL + ``busy_timeout`` so concurrent cross-process read/write is safe.
- Thread-local connections — Hermes dispatches hooks across executor threads.
- Public API is cursor-based (``add_*`` / ``spans(since)`` / ``cursor()``) and is
  unchanged from the original in-memory version, so the tracer and the dashboard
  API don't care that the backend is now SQLite.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _default_db_path() -> str:
    # Next to this module so the gateway and dashboard processes (both loading
    # the plugin from the same install dir) resolve the SAME file.
    return str(Path(__file__).resolve().parent / "live.db")


class LiveStore:
    """Bounded, cross-process telemetry store backed by SQLite (WAL)."""

    def __init__(self, db_path: Optional[str] = None, max_rows: int = 4000) -> None:
        self.db_path = db_path or _default_db_path()
        self.max_rows = max(10, int(max_rows))
        self._local = threading.local()
        self._writes = 0
        self._init_db()

    # ── connection / schema ───────────────────────────────────────────────
    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=3000")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    def _init_db(self) -> None:
        try:
            c = self._conn()
            c.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "  seq  INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  kind TEXT NOT NULL,"
                "  ts   INTEGER NOT NULL,"
                "  data TEXT NOT NULL)"
            )
            c.execute("CREATE INDEX IF NOT EXISTS ix_events_kind_seq ON events(kind, seq)")
            c.commit()
        except Exception:  # pragma: no cover — never break the agent
            pass

    # ── writers (hot path — cheap, never raise) ───────────────────────────
    def _insert(self, kind: str, data: Dict[str, Any]) -> None:
        try:
            c = self._conn()
            c.execute(
                "INSERT INTO events(kind, ts, data) VALUES(?,?,?)",
                (kind, time.time_ns(), json.dumps(data, default=str)),
            )
            self._writes += 1
            # Trim periodically rather than every insert.
            if self._writes % 64 == 0:
                c.execute(
                    "DELETE FROM events WHERE seq <= "
                    "(SELECT COALESCE(MAX(seq), 0) FROM events) - ?",
                    (self.max_rows,),
                )
            c.commit()
        except Exception:  # pragma: no cover
            pass

    def add_span(self, span: Dict[str, Any]) -> None:
        self._insert("span", span)

    def add_metric(self, name: str, value: float, attributes: Dict[str, Any], ts_ns: int) -> None:
        self._insert(
            "metric",
            {
                "name": name,
                "value": value,
                "attributes": dict(attributes or {}),
                "time_unix_nano": ts_ns,
            },
        )

    def add_log(self, record: Dict[str, Any]) -> None:
        self._insert("log", record)

    # ── readers (dashboard API) ───────────────────────────────────────────
    def _query(self, kind: str, since: int, limit: int) -> List[Dict[str, Any]]:
        try:
            c = self._conn()
            rows = c.execute(
                "SELECT seq, data FROM events WHERE kind=? AND seq>? " "ORDER BY seq DESC LIMIT ?",
                (kind, int(since or 0), int(limit) if limit else 1000000),
            ).fetchall()
        except Exception:  # pragma: no cover
            return []
        out: List[Dict[str, Any]] = []
        for seq, data in reversed(rows):  # back to ascending seq
            try:
                d = json.loads(data)
            except Exception:
                continue
            d["seq"] = seq
            out.append(d)
        return out

    def spans(self, since: int = 0, limit: int = 0) -> List[Dict[str, Any]]:
        return self._query("span", since, limit)

    def metrics(self, since: int = 0, limit: int = 0) -> List[Dict[str, Any]]:
        return self._query("metric", since, limit)

    def logs(self, since: int = 0, limit: int = 0) -> List[Dict[str, Any]]:
        return self._query("log", since, limit)

    def cursor(self) -> int:
        try:
            r = self._conn().execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
            return int(r[0]) if r else 0
        except Exception:  # pragma: no cover
            return 0

    def stats(self) -> Dict[str, int]:
        try:
            c = self._conn()
            counts = {
                k: n
                for k, n in c.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall()
            }
        except Exception:  # pragma: no cover
            counts = {}
        return {
            "spans": int(counts.get("span", 0)),
            "metrics": int(counts.get("metric", 0)),
            "logs": int(counts.get("log", 0)),
            "cursor": self.cursor(),
        }

    def clear(self) -> None:
        try:
            c = self._conn()
            c.execute("DELETE FROM events")
            c.execute("DELETE FROM sqlite_sequence WHERE name='events'")
            c.commit()
        except Exception:  # pragma: no cover
            pass


# Per-process singleton; both processes point at the same SQLite file (default
# path), so they share data without sharing memory.
_LIVE_STORE: Optional[LiveStore] = None
_LIVE_LOCK = threading.Lock()


def get_live_store(
    create: bool = False,
    db_path: Optional[str] = None,
    max_rows: int = 4000,
    **_ignored: Any,
) -> Optional[LiveStore]:
    """Return the process-wide :class:`LiveStore`.

    ``create=True`` lazily builds it (the tracer, when ``dashboard_live`` is on).
    The dashboard API calls with ``create=True`` too (read side) so the file is
    opened even if the dashboard process started first. ``None`` only when the
    SQLite backend can't be opened at all.
    """
    global _LIVE_STORE
    if _LIVE_STORE is None and create:
        with _LIVE_LOCK:
            if _LIVE_STORE is None:
                try:
                    _LIVE_STORE = LiveStore(
                        db_path=db_path or os.environ.get("HERMES_OTEL_LIVE_DB"),
                        max_rows=max_rows,
                    )
                except Exception:  # pragma: no cover
                    return None
    return _LIVE_STORE
