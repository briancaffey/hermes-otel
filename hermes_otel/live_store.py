"""Shared, bounded telemetry store for the zero-config dashboard.

The plugin's hooks run in the **gateway** process; the dashboard is served by a
**separate** ``hermes dashboard`` process. They don't share memory, so the live
store is backed by a small **SQLite** file (WAL mode) that both processes open:
the gateway writes recent spans / metrics / logs, the dashboard reads them.

Design (schema v2, #184):
- One append-only ``events`` table: ``seq`` (autoincrement = the cursor shared
  by every kind), a ``kind`` discriminator (span/metric/log), the full JSON
  ``data`` blob, and **indexed columns extracted at insert time** (trace id,
  session id, name, status, start/end, level, logger, metric name, value) so
  the dashboard can filter, group and bucket in SQL instead of shipping the
  whole buffer to the browser.
- Bounded two ways: each kind is trimmed to its last ``max_rows`` rows, and
  rows older than ``retention_hours`` are dropped (both cheap, periodic).
- WAL + ``busy_timeout`` so concurrent cross-process read/write is safe.
- Thread-local connections: Hermes dispatches hooks across executor threads.
- The cursor-based API (``add_*`` / ``spans(since)`` / ``cursor()``) is
  unchanged from v1; the query API (``query_traces`` and friends) is new.
- Schema versions (``PRAGMA user_version``): v1 (plugin < 1.9) was a JSON
  blob only and is dropped and recreated on first open. v2 (1.9 to 1.18) and
  v3 (1.19, #266: log rows carry the OTel severity spelling, span id and
  attributes inside the JSON) share the table layout, so a v2 file is kept and
  stamped v3 in place: a dashboard running 1.19 can read a file an older
  gateway is still writing. A file stamped with a newer version than this
  module knows is opened read-compatibly and never re-stamped.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, TypeVar

from . import semconv as _sc
from .buckets import bucketize
from .coerce import optional_number
from .live_summary import _session_of, summarize_trace
from .severity import python_level

T = TypeVar("T")

LIVE_DB_FILENAME = "hermes_otel_live.db"
SCHEMA_VERSION = 3  # v3 (#266): log rows carry OTel severity spelling, span_id, attributes
# Rows a failed commit may hold back for the next attempt (#291); beyond this
# the oldest are dropped so a backend that stays locked cannot grow memory.
MAX_PENDING_ROWS = 5000
# Host samples (CPU, GPU, power) arrive several times a second when
# ``host_metrics`` is on; they get their own small cap so they can never evict
# the turn's token and cost points from the metric buffer (#291).
HOST_SAMPLE_MAX_ROWS = 200
_HOST_METRIC_SQL = "(name LIKE 'process.cpu.%' OR name LIKE 'system.cpu.%' OR name LIKE 'hw.%')"

# The vocabulary lives in ``semconv`` (#404); these names stay for the readers.
_MODEL_KEYS = _sc.MODEL_KEYS
_KIND_PREFIXES: Dict[str, Tuple[str, ...]] = _sc.KIND_PREFIXES


def _like(text: str) -> str:
    r"""Escape a user string for ``LIKE ... ESCAPE '\'`` (``%`` and ``_`` are literal)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _json_attr(key: str) -> str:
    """SQL expression for one attribute of the row's JSON blob (dotted keys quoted)."""
    return "json_extract(data, '$.attributes.\"" + key.replace('"', "") + "\"')"


def _restrict_to_owner(path: str) -> None:
    """``chmod 0600`` the file at *path*; never raises.

    The store holds full prompts, responses and tool I/O, so it must not be
    readable by other local accounts the way the process umask (usually
    ``022``) would leave it. Called right after the SQLite file is opened and
    before WAL mode is enabled: SQLite gives the ``-wal`` / ``-shm`` sidecars
    the mode of the main database file, so tightening the database first
    covers all three. On Windows ``chmod`` only touches the read-only bit,
    which ``0o600`` leaves clear.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def default_db_path_for(home: Path) -> str:
    """The live store file for a given ``HERMES_HOME`` (pure; tests use this)."""
    return str(Path(home) / LIVE_DB_FILENAME)


def _default_db_path() -> str:
    """``$HERMES_HOME/hermes_otel_live.db`` (override with ``HERMES_OTEL_LIVE_DB``).

    Both the gateway and the dashboard process resolve the same ``HERMES_HOME``,
    so they share the file. It used to live next to this module inside the
    plugin directory, which ``hermes plugins install`` wipes on every upgrade
    and which is read-only for site-packages installs (#100). The test suite
    monkeypatches this function to a temp dir, so tests must not call it to
    check the default: use :func:`default_db_path_for`.
    """
    from .plugin_config import hermes_home

    return default_db_path_for(hermes_home())


class LiveStore:
    """Bounded, cross-process telemetry store backed by SQLite (WAL)."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        max_rows: int = 4000,
        retention_hours: float = 168.0,
    ) -> None:
        self.db_path = db_path or _default_db_path()
        self.max_rows = max(10, int(max_rows))
        self.retention_ns = int(max(0.0, float(retention_hours)) * 3600 * 1e9)
        self._local = threading.local()
        self._conns: list = []  # (owner thread, connection) still open, for close()
        self._conns_lock = threading.Lock()
        self._writes = 0
        # Writes are buffered and committed in one transaction by a daemon
        # thread every ``flush_interval_s`` or ``batch_rows`` rows (#91), so a
        # hook never pays a disk commit. Readers flush first, so a process
        # reads its own writes; another process sees them within the interval.
        self.flush_interval_s = 0.25
        self.batch_rows = 64
        self._pending: List[tuple] = []
        self._pending_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._writer: Optional[threading.Thread] = None
        # Why the file could not be opened or initialised, if it could not;
        # ``stats()`` reports it so the dashboard can say "corrupt file" rather
        # than "no telemetry yet" (#291).
        self.open_error: Optional[str] = None
        self.last_write_error: Optional[str] = None
        self._last_error_log = 0.0
        self._init_db()

    # ── connection / schema ───────────────────────────────────────────────
    def close(self) -> None:
        """Flush pending rows, stop the writer and close every connection (idempotent)."""
        self._stop.set()
        self._wake.set()
        writer = self._writer
        if writer is not None and writer.is_alive() and writer is not threading.current_thread():
            writer.join(timeout=2.0)
        self._writer = None
        self.flush()
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for _owner, c in conns:
            try:
                c.close()
            except Exception:  # pragma: no cover: never raise on shutdown
                pass
        self._local.conn = None

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
            _restrict_to_owner(self.db_path)  # before WAL: sidecars inherit this mode
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=3000")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
            me = threading.current_thread()
            with self._conns_lock:
                # A thread-local connection outlives its thread (sqlite3
                # connections can't be weakref'd, and _conns keeps them
                # alive). Close the ones whose owner thread has exited, or a
                # gateway serving reads/writes from transient worker threads
                # leaks 2 fds (db + wal) per thread until it crosses
                # FD_SETSIZE and select() callers in the process break.
                live = []
                for owner, conn in self._conns:
                    if owner.is_alive() or owner is me:
                        live.append((owner, conn))
                    else:
                        try:
                            conn.close()
                        except Exception:  # pragma: no cover
                            pass
                live.append((me, c))
                self._conns = live
        return c

    # Indexed columns added since v2, in the order they were introduced; an
    # older file gets the missing ones with ALTER TABLE instead of a DROP.
    _COLUMNS: Tuple[Tuple[str, str], ...] = (
        ("trace_id", "TEXT"),
        ("span_id", "TEXT"),
        ("parent_span_id", "TEXT"),
        ("session_id", "TEXT"),
        ("name", "TEXT"),
        ("status", "TEXT"),
        ("start_ns", "integer"),
        ("end_ns", "integer"),
        ("duration_ms", "REAL"),
        ("level", "integer"),
        ("logger", "TEXT"),
        ("value", "REAL"),
    )

    def _init_db(self) -> None:
        try:
            c = self._conn()
            version = int(c.execute("PRAGMA user_version").fetchone()[0])
            has_table = bool(
                c.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
                ).fetchone()
            )
            if has_table:
                existing = {r[1] for r in c.execute("PRAGMA table_info(events)").fetchall()}
                if version < 2 or "trace_id" not in existing:
                    # v1 layout (JSON blob only, plugin < 1.9). The buffer is
                    # recent activity, not a record; start over with the
                    # indexed layout.
                    c.execute("DROP TABLE events")
                    c.execute("DELETE FROM sqlite_sequence WHERE name='events'")
                    has_table = False
                else:
                    # v2 → v3 in place: same layout, newer JSON shape for log
                    # rows; add any column a later version introduced so an
                    # older gateway can keep writing the file we read (#291).
                    for col, typ in self._COLUMNS:
                        if col not in existing:
                            c.execute(f"ALTER TABLE events ADD COLUMN {col} {typ}")
            c.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "  seq  INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  kind TEXT NOT NULL,"
                "  ts   INTEGER NOT NULL,"
                "  data TEXT NOT NULL,"
                "  trace_id TEXT, span_id TEXT, parent_span_id TEXT, session_id TEXT,"
                "  name TEXT, status TEXT, start_ns INTEGER, end_ns INTEGER,"
                "  duration_ms REAL, level INTEGER, logger TEXT, value REAL)"
            )
            for ix, cols in (
                ("ix_events_kind_seq", "kind, seq"),
                ("ix_events_kind_trace", "kind, trace_id"),
                ("ix_events_kind_session", "kind, session_id"),
                ("ix_events_kind_start", "kind, start_ns"),
                ("ix_events_kind_name_ts", "kind, name, ts"),
                ("ix_events_kind_ts", "kind, ts"),
            ):
                c.execute(f"CREATE INDEX IF NOT EXISTS {ix} ON events({cols})")
            if version < SCHEMA_VERSION:
                # Never re-stamp a file a newer plugin wrote (a downgrade):
                # it stays readable and the newer plugin finds it intact.
                c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            # One trim per open, so a store nobody writes 64 rows to at a
            # time (one-shot runs) is still bounded by cap and age (#291).
            self._trim(c, ("span", "metric", "log"))
            c.commit()
        except Exception as e:  # never break the agent; say why instead
            self.open_error = f"{type(e).__name__}: {e}"
            self._log_error(f"live store: cannot open {self.db_path}: {self.open_error}")

    def _log_error(self, msg: str) -> None:
        """One debug-log line per 30 s at most; never raises."""
        now = time.monotonic()
        if now - self._last_error_log < 30.0:
            return
        self._last_error_log = now
        try:
            from .debug_utils import debug_log

            debug_log(msg)
        except Exception:  # pragma: no cover
            pass

    def _trim(self, c: sqlite3.Connection, kinds: Iterable[str]) -> None:
        """Enforce the per-kind row cap, the host-sample cap and the age limit."""
        for kind in kinds:
            if kind == "metric":
                c.execute(
                    "DELETE FROM events WHERE kind = 'metric' AND NOT " + _HOST_METRIC_SQL + " "
                    "AND seq NOT IN (SELECT seq FROM events WHERE kind = 'metric' AND NOT "
                    + _HOST_METRIC_SQL
                    + " ORDER BY seq DESC LIMIT ?)",
                    (self.max_rows,),
                )
                c.execute(
                    "DELETE FROM events WHERE kind = 'metric' AND " + _HOST_METRIC_SQL + " "
                    "AND seq NOT IN (SELECT seq FROM events WHERE kind = 'metric' AND "
                    + _HOST_METRIC_SQL
                    + " ORDER BY seq DESC LIMIT ?)",
                    (HOST_SAMPLE_MAX_ROWS,),
                )
                continue
            c.execute(
                "DELETE FROM events WHERE kind = ? AND seq NOT IN "
                "(SELECT seq FROM events WHERE kind = ? ORDER BY seq DESC LIMIT ?)",
                (kind, kind, self.max_rows),
            )
        if self.retention_ns:
            c.execute("DELETE FROM events WHERE ts < ?", (time.time_ns() - self.retention_ns,))

    # ── writers (hot path: cheap, never raise) ────────────────────────────
    def _insert(
        self, kind: str, data: Dict[str, Any], ts: Optional[int] = None, **cols: Any
    ) -> None:
        """Queue one row; the writer thread commits it with its batch."""
        try:
            row = (
                kind,
                int(ts or time.time_ns()),
                json.dumps(data, default=str),
                tuple(cols.keys()),
                tuple(cols.values()),
            )
        except Exception:  # pragma: no cover
            return
        with self._pending_lock:
            self._pending.append(row)
            n = len(self._pending)
        self._ensure_writer()
        if n >= self.batch_rows:
            self._wake.set()

    def _ensure_writer(self) -> None:
        if self._writer is not None and self._writer.is_alive():
            return
        if self._stop.is_set():
            return
        t = threading.Thread(target=self._writer_loop, name="hermes-otel-live-store", daemon=True)
        self._writer = t
        t.start()

    def _writer_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.flush_interval_s)
            self._wake.clear()
            self.flush()

    def flush(self) -> int:
        """Commit every pending row in one transaction. Returns rows written.

        Serialised with the writer thread: a caller that flushes before a read
        waits for a batch the writer is committing, then commits the rest, so
        the read that follows sees every row queued before it.
        """
        written = 0
        with self._flush_lock:
            with self._pending_lock:
                rows, self._pending = self._pending, []
            if not rows:
                return 0
            try:
                c = self._conn()
                kinds = set()
                for kind, ts, data, names, values in rows:
                    cols = ["kind", "ts", "data"] + list(names)
                    c.execute(
                        f"INSERT INTO events({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                        [kind, ts, data] + list(values),
                    )
                    kinds.add(kind)
                    written += 1
                before, self._writes = self._writes, self._writes + written
                # Trim every 64 writes (and once per open, see _init_db): per
                # kind, so a chatty logger cannot evict every span (#100), and
                # by age (#184).
                if before // 64 != self._writes // 64:
                    self._trim(c, kinds)
                c.commit()
                self.last_write_error = None
            except Exception as e:
                # The batch is NOT lost: put it back at the front, bounded, so
                # a lock held by another process or a transient disk error
                # costs a delay rather than a turn (#291). SQLite rolled the
                # partial transaction back when the statement failed.
                try:
                    c.rollback()
                except Exception:  # pragma: no cover
                    pass
                written = 0
                self.last_write_error = f"{type(e).__name__}: {e}"
                with self._pending_lock:
                    self._pending = (rows + self._pending)[-MAX_PENDING_ROWS:]
                    held = len(self._pending)
                self._log_error(
                    f"live store: commit to {self.db_path} failed ({self.last_write_error}); "
                    f"{held} rows held for the next attempt"
                )
                self._wake.set()
        return written

    def add_span(self, span: Dict[str, Any]) -> None:
        attrs = span.get("attributes") or {}
        start = int(span.get("start_time_unix_nano") or 0) or None
        end = int(span.get("end_time_unix_nano") or 0) or None
        self._insert(
            "span",
            span,
            ts=end or start,
            trace_id=span.get("trace_id"),
            span_id=span.get("span_id"),
            parent_span_id=span.get("parent_span_id"),
            session_id=_session_of(attrs) if isinstance(attrs, dict) else None,
            name=span.get("name"),
            status=span.get("status"),
            start_ns=start,
            end_ns=end,
            duration_ms=optional_number(span.get("duration_ms")),
        )

    def add_metric(self, name: str, value: float, attributes: Dict[str, Any], ts_ns: int) -> None:
        self._insert(
            "metric",
            {
                "name": name,
                "value": value,
                "attributes": dict(attributes or {}),
                "time_unix_nano": ts_ns,
            },
            ts=ts_ns or None,
            name=name,
            value=optional_number(value),
        )

    def add_log(self, record: Dict[str, Any]) -> None:
        self._insert(
            "log",
            record,
            ts=int(record.get("time_unix_nano") or 0) or None,
            trace_id=record.get("trace_id"),
            span_id=record.get("span_id"),
            session_id=record.get("session_id"),
            name=record.get("event_name") or record.get("logger"),
            level=python_level(record.get("level")),
            logger=record.get("logger"),
        )

    # ── readers ───────────────────────────────────────────────────────────
    def _read(self, default: T, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Flush, then run ``fn`` on this thread's connection; ``default`` on failure.

        The one guard every reader goes through (#518): a corrupt or locked
        file answers "no telemetry" to the caller and says why in the debug
        log (rate-limited by :meth:`_log_error`), instead of ten silent copies.
        """
        self.flush()
        try:
            return fn(self._conn())
        except Exception as e:
            self._log_error(
                f"live store: read from {self.db_path} failed ({type(e).__name__}: {e})"
            )
            return default

    # ── cursor readers (dashboard API, unchanged since v1) ────────────────
    @staticmethod
    def _rows_to_dicts(rows: Iterable) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for seq, data in rows:
            try:
                d = json.loads(data)
            except Exception:
                continue
            d["seq"] = seq
            out.append(d)
        return out

    def _query(self, kind: str, since: int, limit: int) -> List[Dict[str, Any]]:
        rows = self._read(
            [],
            lambda c: c.execute(
                "SELECT seq, data FROM events WHERE kind=? AND seq>? ORDER BY seq DESC LIMIT ?",
                (kind, int(since or 0), int(limit) if limit else 1000000),
            ).fetchall(),
        )
        return self._rows_to_dicts(reversed(rows))  # back to ascending seq

    def spans(self, since: int = 0, limit: int = 0) -> List[Dict[str, Any]]:
        return self._query("span", since, limit)

    def metrics(self, since: int = 0, limit: int = 0) -> List[Dict[str, Any]]:
        return self._query("metric", since, limit)

    def logs(self, since: int = 0, limit: int = 0) -> List[Dict[str, Any]]:
        return self._query("log", since, limit)

    def cursor(self) -> int:
        return self._read(
            0, lambda c: int(c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0])
        )

    def stats(self) -> Dict[str, Any]:
        """Row counts per kind and the cursor; ``open_error`` / ``write_error``
        when the file could not be opened or the last commit failed."""
        counts = self._read(
            {},
            lambda c: dict(c.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall()),
        )
        out: Dict[str, Any] = {
            "spans": int(counts.get("span", 0)),
            "metrics": int(counts.get("metric", 0)),
            "logs": int(counts.get("log", 0)),
            "cursor": self.cursor(),
        }
        if self.open_error:
            out["open_error"] = self.open_error
        if self.last_write_error:
            out["write_error"] = self.last_write_error
        with self._pending_lock:
            if self._pending:
                out["pending"] = len(self._pending)
        return out

    def clear(self) -> None:
        with self._pending_lock:
            self._pending.clear()
        try:
            c = self._conn()
            c.execute("DELETE FROM events")
            c.execute("DELETE FROM sqlite_sequence WHERE name='events'")
            c.commit()
        except Exception:  # pragma: no cover
            pass

    # ── query API (server-side filtering, #184) ───────────────────────────
    def _span_where(
        self,
        start_ns: Optional[int],
        end_ns: Optional[int],
        session: Optional[str],
        status: Optional[str],
        name: Optional[str],
        kind: Optional[str],
        text: Optional[str],
        trace_id: Optional[str] = None,
        min_duration_ms: Optional[float] = None,
        model: Optional[str] = None,
        tool: Optional[str] = None,
    ):
        where = ["kind='span'"]
        args: List[Any] = []
        if min_duration_ms:
            where.append("duration_ms >= ?")
            args.append(float(min_duration_ms))
        if model:
            # A substring of the model *attribute* only, never of the whole
            # blob: a model name quoted in a prompt is not a match (#291).
            parts = [f"{_json_attr(k)} LIKE ? ESCAPE '\\'" for k in _MODEL_KEYS]
            where.append("(" + " OR ".join(parts) + ")")
            args.extend([f"%{_like(model)}%"] * len(_MODEL_KEYS))
        if tool:
            where.append("name = ?")
            args.append(f"tool.{tool}" if not tool.startswith("tool.") else tool)
        if start_ns:
            where.append("start_ns >= ?")
            args.append(int(start_ns))
        if end_ns:
            where.append("start_ns <= ?")
            args.append(int(end_ns))
        if session:
            where.append("session_id = ?")
            args.append(session)
        if status == "error":
            where.append("status = 'ERROR'")
        elif status == "ok":
            # A trace is ok when NONE of its spans failed, not when one span
            # happened to succeed (#291).
            where.append(
                "trace_id NOT IN (SELECT trace_id FROM events WHERE kind='span' "
                "AND status = 'ERROR' AND trace_id IS NOT NULL)"
            )
        if name:
            where.append("name LIKE ? ESCAPE '\\'")
            args.append(f"%{_like(name)}%")
        if kind:
            where.append(self._kind_sql(kind, args))
        if text:
            where.append("data LIKE ? ESCAPE '\\'")
            args.append(f"%{_like(text)}%")
        if trace_id:
            where.append("trace_id = ?")
            args.append(trace_id)
        return " AND ".join(where), args

    @staticmethod
    def _kind_sql(kind: str, args: List[Any]) -> str:
        """SQL for one span kind, mirroring :func:`semconv.span_kind` (prefix or
        ``hermes.span_kind`` attribute); ``other`` is none of the known kinds."""
        # COALESCE: a missing attribute is NULL, and NOT (NULL OR …) would be
        # NULL rather than true, hiding every row from ``kind=other``.
        attr = "COALESCE(" + _json_attr(_sc.KIND_OVERRIDE_ATTR) + ", '')"

        def one(k: str) -> str:
            clauses = []
            for prefix in _KIND_PREFIXES[k]:
                clauses.append("name LIKE ? ESCAPE '\\'")
                args.append(f"{_like(prefix)}%")
            if k in _sc.KIND_OVERRIDES:
                clauses.append(f"{attr} = ?")
                args.append(k)
            return "(" + " OR ".join(clauses) + ")"

        if kind == "other":
            return "NOT (" + " OR ".join(one(k) for k in _KIND_PREFIXES) + ")"
        if kind not in _KIND_PREFIXES:
            args.append(f"{_like(kind)}.%")
            return "name LIKE ? ESCAPE '\\'"
        return one(kind)

    def spans_for_traces(self, trace_ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
        """All stored spans of the given traces, grouped by trace id."""
        out: Dict[str, List[Dict[str, Any]]] = {t: [] for t in trace_ids}
        if not trace_ids:
            return out

        def fill(c: sqlite3.Connection) -> Dict[str, List[Dict[str, Any]]]:
            for chunk_start in range(0, len(trace_ids), 500):
                chunk = list(trace_ids[chunk_start : chunk_start + 500])
                marks = ", ".join("?" * len(chunk))
                rows = c.execute(
                    f"SELECT seq, data FROM events WHERE kind='span' AND trace_id IN ({marks}) "
                    "ORDER BY seq ASC",
                    chunk,
                ).fetchall()
                for d in self._rows_to_dicts(rows):
                    out.setdefault(d.get("trace_id"), []).append(d)
            return out

        return self._read(out, fill)

    def query_traces(
        self,
        start_ns: Optional[int] = None,
        end_ns: Optional[int] = None,
        session: Optional[str] = None,
        status: Optional[str] = None,
        name: Optional[str] = None,
        kind: Optional[str] = None,
        text: Optional[str] = None,
        trace_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        min_duration_ms: Optional[float] = None,
        model: Optional[str] = None,
        tool: Optional[str] = None,
        before_ns: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Trace-list rows, newest start first, whose spans match every filter.

        Paging is by keyset: ``before_ns`` keeps only traces whose start is
        strictly older than it, and ``next_before_ns`` in the result is the
        start of the oldest row returned (``None`` when empty), so a turn
        landing between two pages never shifts the older page. ``offset``
        still works for callers that use it. ``total`` counts every matching
        trace regardless of the cursor; ``has_more`` says whether rows exist
        past this page.
        """
        where, args = self._span_where(
            start_ns,
            end_ns,
            session,
            status,
            name,
            kind,
            text,
            trace_id,
            min_duration_ms,
            model,
            tool,
        )
        # The cursor and the order use the same key (the earliest matching
        # span's start), so pages are gap-free and duplicate-free.
        grouped = (
            f"SELECT trace_id, MIN(COALESCE(start_ns, ts)) AS first FROM events "
            f"WHERE {where} AND trace_id IS NOT NULL GROUP BY trace_id"
        )
        having = " HAVING first < ?" if before_ns else ""
        cursor_args = [int(before_ns)] if before_ns else []

        def page(c: sqlite3.Connection) -> Tuple[int, int, List[tuple]]:
            total = int(
                c.execute(
                    f"SELECT COUNT(DISTINCT trace_id) FROM events WHERE {where}", args
                ).fetchone()[0]
            )
            remaining = total
            if before_ns:
                remaining = int(
                    c.execute(
                        f"SELECT COUNT(*) FROM ({grouped}{having})", args + cursor_args
                    ).fetchone()[0]
                )
            rows = c.execute(
                f"{grouped}{having} ORDER BY first DESC LIMIT ? OFFSET ?",
                args + cursor_args + [int(limit), int(offset)],
            ).fetchall()
            return total, remaining, rows

        # On a read failure the default yields the empty envelope below.
        total, remaining, rows = self._read((0, 0, []), page)
        ids = [r[0] for r in rows]
        by_trace = self.spans_for_traces(ids)
        traces = [summarize_trace(by_trace[t]) for t in ids if by_trace.get(t)]
        return {
            "traces": traces,
            "total": total,
            "has_more": int(offset) + len(ids) < remaining,
            "next_before_ns": int(rows[-1][1]) if rows else None,
        }

    def trace(self, trace_id: str) -> Dict[str, Any]:
        spans = self.spans_for_traces([trace_id]).get(trace_id) or []
        if not spans:
            return {"trace": None, "spans": []}
        return {"trace": summarize_trace(spans), "spans": spans}

    def sessions(
        self,
        start_ns: Optional[int] = None,
        end_ns: Optional[int] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """One row per session id: turns, span/error counts, totals, first/last.

        Every trace in the window that carries a session id is grouped (the
        store is bounded by its row cap, so this is at most ``max_rows``
        spans); ``has_more`` says whether more sessions than ``limit`` exist.
        """
        where, args = self._span_where(start_ns, end_ns, None, None, None, None, None)
        ids = [
            r[0]
            for r in self._read(
                [],
                lambda c: c.execute(
                    f"SELECT trace_id FROM events WHERE {where} AND session_id IS NOT NULL "
                    "AND trace_id IS NOT NULL GROUP BY trace_id "
                    "ORDER BY MAX(COALESCE(end_ns, start_ns, ts)) DESC",
                    args,
                ).fetchall(),
            )
        ]
        by_trace = self.spans_for_traces(ids)
        sessions: Dict[str, Dict[str, Any]] = {}
        for t in ids:
            spans = by_trace.get(t)
            if not spans:
                continue
            s = summarize_trace(spans)
            sid = s.get("session")
            if not sid:
                continue
            row = sessions.setdefault(
                sid,
                {
                    "session": sid,
                    "turns": 0,
                    "spans": 0,
                    "errors": 0,
                    "tokens": 0.0,
                    "cost": 0.0,
                    "toolCalls": 0,
                    "subagentTokens": 0.0,
                    "startNs": s["startNs"],
                    "endNs": s["endNs"],
                    "platform": s.get("platform"),
                    "model": s.get("model"),
                    "traceIds": [],
                },
            )
            row["turns"] += 1
            row["spans"] += s["spanCount"]
            row["errors"] += sum(1 for sp in spans if sp.get("status") == "ERROR")
            row["tokens"] += s["tokens"] or 0
            row["cost"] += s["cost"] or 0
            row["subagentTokens"] += (s.get("subagents") or {}).get("tokens") or 0
            row["toolCalls"] += sum(
                1 for sp in spans if str(sp.get("name", "")).startswith("tool.")
            )
            row["startNs"] = min(row["startNs"], s["startNs"])
            row["endNs"] = max(row["endNs"], s["endNs"])
            row["traceIds"].append(t)
        ordered = sorted(sessions.values(), key=lambda r: r["endNs"], reverse=True)
        rows = ordered[: int(limit)]
        for r in rows:
            r["tokens"] = r["tokens"] or None
            r["cost"] = r["cost"] or None
            r["subagentTokens"] = r["subagentTokens"] or None
        return {"sessions": rows, "has_more": len(ordered) > len(rows)}

    def metric_names(self) -> List[Dict[str, Any]]:
        rows = self._read(
            [],
            lambda c: c.execute(
                "SELECT name, COUNT(*), MAX(ts) FROM events WHERE kind='metric' GROUP BY name ORDER BY name"
            ).fetchall(),
        )
        return [{"name": n, "count": int(c), "lastTs": int(t or 0)} for n, c, t in rows]

    def metric_buckets(
        self,
        name: str,
        start_ns: int,
        end_ns: int,
        bucket_s: int,
        group_by: Optional[str] = None,
        agg: str = "sum",
    ) -> Dict[str, Any]:
        """Aggregate one instrument into fixed time buckets, optionally per attribute value.

        ``points`` counts every stored row of the instrument in the window,
        valueless rows included (:func:`bucketize`).
        """
        bucket_ns = max(1, int(bucket_s)) * 1_000_000_000
        lo = int(start_ns) - int(start_ns) % bucket_ns
        rows = self._read(
            [],
            lambda c: c.execute(
                "SELECT ts, value, data FROM events WHERE kind='metric' AND name=? AND ts>=? AND ts<=?",
                (name, lo, int(end_ns)),
            ).fetchall(),
        )

        def label_of(data: str) -> str:
            if not group_by:
                return "_"
            try:
                return str(json.loads(data).get("attributes", {}).get(group_by, "—"))
            except Exception:
                return "—"

        points = [(ts, value, label_of(data)) for ts, value, data in rows]
        return {"name": name, **bucketize(points, lo, end_ns, bucket_s, agg)}

    def query_logs(
        self,
        trace_id: Optional[str] = None,
        session: Optional[str] = None,
        level_min: Optional[int] = None,
        logger: Optional[str] = None,
        text: Optional[str] = None,
        start_ns: Optional[int] = None,
        end_ns: Optional[int] = None,
        limit: int = 300,
        before_ns: Optional[int] = None,
        span_id: Optional[str] = None,
        event_name: Optional[str] = None,
        events_only: bool = False,
    ) -> List[Dict[str, Any]]:
        where = ["kind='log'"]
        args: List[Any] = []
        if trace_id:
            where.append("trace_id = ?")
            args.append(trace_id)
        if span_id:
            where.append("span_id = ?")
            args.append(span_id)
        if event_name:
            where.append("json_extract(data, '$.event_name') = ?")
            args.append(event_name)
        elif events_only:
            where.append("json_extract(data, '$.event_name') IS NOT NULL")
        if session:
            where.append("session_id = ?")
            args.append(session)
        if level_min:
            where.append("level >= ?")
            args.append(int(level_min))
        if logger:
            where.append("logger = ?")
            args.append(logger)
        if text:
            where.append("data LIKE ? ESCAPE '\\'")
            args.append(f"%{_like(text)}%")
        if start_ns:
            where.append("ts >= ?")
            args.append(int(start_ns))
        if end_ns:
            where.append("ts <= ?")
            args.append(int(end_ns))
        if before_ns:
            # Keyset paging: the page after the one that ended at ``before_ns``.
            where.append("ts < ?")
            args.append(int(before_ns))
        rows = self._read(
            [],
            lambda c: c.execute(
                f"SELECT seq, data FROM events WHERE {' AND '.join(where)} ORDER BY seq DESC LIMIT ?",
                args + [int(limit)],
            ).fetchall(),
        )
        return self._rows_to_dicts(rows)  # newest first

    def loggers(self) -> List[Dict[str, Any]]:
        rows = self._read(
            [],
            lambda c: c.execute(
                "SELECT logger, COUNT(*) FROM events WHERE kind='log' AND logger IS NOT NULL "
                "GROUP BY logger ORDER BY COUNT(*) DESC"
            ).fetchall(),
        )
        return [{"logger": n, "count": int(c)} for n, c in rows]
