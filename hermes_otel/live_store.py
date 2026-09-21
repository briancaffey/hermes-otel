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
- A v1 file (no ``user_version``) is a bounded buffer of recent activity, not a
  record: it is dropped and recreated with the v2 schema on first open.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

LIVE_DB_FILENAME = "hermes_otel_live.db"
SCHEMA_VERSION = 2

_SESSION_KEYS = ("hermes.session_id", "session.id", "session_id")
_MODEL_KEYS = ("gen_ai.request.model", "llm.model_name", "gen_ai.response.model")
_TOKEN_KEYS = ("gen_ai.usage.total_tokens", "llm.token_count.total")
_COST_KEYS = ("hermes.cost.usage",)


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


# ── pure helpers (also used by the dashboard API) ─────────────────────────


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _first(attrs: Dict[str, Any], keys: Sequence[str]) -> Any:
    for k in keys:
        v = attrs.get(k)
        if v not in (None, ""):
            return v
    return None


def _session_of(attrs: Dict[str, Any]) -> Optional[str]:
    v = _first(attrs, _SESSION_KEYS)
    return str(v) if v is not None else None


def span_kind(name: str, attrs: Optional[Dict[str, Any]] = None) -> str:
    """Same classification as ``kindOf`` in ``dashboard-ui/src/lib.ts``."""
    a = attrs or {}
    if a.get("hermes.span_kind") == "skill":
        return "skill"
    if a.get("hermes.span_kind") == "approval":
        return "approval"
    n = (name or "").lower()
    if n == "agent" or n.startswith("agent."):
        return "agent"
    if n.startswith("cron"):
        return "cron"
    if n.startswith("session"):
        return "session"
    if n.startswith("skill."):
        return "skill"
    if n.startswith("approval"):
        return "approval"
    if n.startswith("subagent"):
        return "subagent"
    if n.startswith("llm."):
        return "llm"
    if n.startswith("api."):
        return "api"
    if n.startswith("tool."):
        return "tool"
    return "other"


def trace_totals(spans: Iterable[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Tokens and cost for ONE trace, counted once (#178).

    The ``agent`` root already carries the turn's totals and every ``api.*``
    span carries its own call, so summing all spans doubled every turn. Use
    the root's figure when it has one; otherwise sum the ``api.*`` spans only
    (``llm.*`` spans mirror the API spans). Mirrors ``traceTotals`` in
    ``dashboard-ui/src/lib.ts``.
    """
    spans = list(spans)
    ids = {s.get("span_id") for s in spans}
    root = next(
        (s for s in spans if not s.get("parent_span_id") or s.get("parent_span_id") not in ids),
        None,
    )

    def pick(keys: Sequence[str]) -> Optional[float]:
        if root is not None:
            v = _num(_first(root.get("attributes") or {}, keys))
            if v is not None:
                return v
        total, seen = 0.0, False
        for s in spans:
            if not str(s.get("name", "")).startswith("api."):
                continue
            v = _num(_first(s.get("attributes") or {}, keys))
            if v is not None:
                total += v
                seen = True
        return total if seen else None

    return {"tokens": pick(_TOKEN_KEYS), "cost": pick(_COST_KEYS)}


def summarize_trace(spans: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One trace-list row from its spans (the shape ``LiveTrace`` uses in the UI)."""
    ids = {s.get("span_id") for s in spans}
    root = next(
        (s for s in spans if not s.get("parent_span_id") or s.get("parent_span_id") not in ids),
        spans[0],
    )
    start = min(int(s.get("start_time_unix_nano") or 0) for s in spans)
    end = max(int(s.get("end_time_unix_nano") or s.get("start_time_unix_nano") or 0) for s in spans)
    totals = trace_totals(spans)
    model = None
    for s in spans:
        model = _first(s.get("attributes") or {}, _MODEL_KEYS)
        if model:
            break
    rattrs = root.get("attributes") or {}
    return {
        "traceId": root.get("trace_id"),
        "rootName": root.get("name"),
        "rootKind": span_kind(root.get("name") or "", rattrs),
        "service": rattrs.get("service.name") or "hermes",
        "startNs": start,
        "endNs": end,
        "durationMs": (end - start) / 1e6,
        "spanCount": len(spans),
        "model": model,
        "tokens": totals["tokens"],
        "cost": totals["cost"],
        "error": any(s.get("status") == "ERROR" for s in spans),
        "session": _session_of(rattrs)
        or next(
            (
                _session_of(s.get("attributes") or {})
                for s in spans
                if _session_of(s.get("attributes") or {})
            ),
            None,
        ),
        "turn": _num(rattrs.get("hermes.turn.number")),
        "platform": rattrs.get("hermes.platform"),
    }


_LEVELS = {"debug": 10, "info": 20, "warning": 30, "warn": 30, "error": 40, "critical": 50}


def _level_no(level: Any) -> int:
    return _LEVELS.get(str(level or "info").lower(), 20)


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
        self._conns: list = []  # every connection ever opened, for close()
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
        for c in conns:
            try:
                c.close()
            except Exception:  # pragma: no cover: never raise on shutdown
                pass
        self._local.conn = None

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=3000")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
            with self._conns_lock:
                self._conns.append(c)
        return c

    def _init_db(self) -> None:
        try:
            c = self._conn()
            version = int(c.execute("PRAGMA user_version").fetchone()[0])
            has_table = bool(
                c.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
                ).fetchone()
            )
            if has_table and version < SCHEMA_VERSION:
                # v1 layout (JSON blob only). The buffer is recent activity, not
                # a record; start over with the indexed layout.
                c.execute("DROP TABLE events")
                c.execute("DELETE FROM sqlite_sequence WHERE name='events'")
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
            c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            c.commit()
        except Exception:  # pragma: no cover: never break the agent
            pass

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
                # Trim every 64 writes, as before: per kind, so a chatty logger
                # cannot evict every span (#100), and by age (#184).
                if before // 64 != self._writes // 64:
                    for kind in kinds:
                        c.execute(
                            "DELETE FROM events WHERE kind = ? AND seq NOT IN "
                            "(SELECT seq FROM events WHERE kind = ? ORDER BY seq DESC LIMIT ?)",
                            (kind, kind, self.max_rows),
                        )
                    if self.retention_ns:
                        c.execute(
                            "DELETE FROM events WHERE ts < ?",
                            (time.time_ns() - self.retention_ns,),
                        )
                c.commit()
            except Exception:  # pragma: no cover
                pass
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
            duration_ms=_num(span.get("duration_ms")),
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
            value=_num(value),
        )

    def add_log(self, record: Dict[str, Any]) -> None:
        self._insert(
            "log",
            record,
            ts=int(record.get("time_unix_nano") or 0) or None,
            trace_id=record.get("trace_id"),
            session_id=record.get("session_id"),
            name=record.get("logger"),
            level=_level_no(record.get("level")),
            logger=record.get("logger"),
        )

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
        self.flush()
        try:
            c = self._conn()
            rows = c.execute(
                "SELECT seq, data FROM events WHERE kind=? AND seq>? ORDER BY seq DESC LIMIT ?",
                (kind, int(since or 0), int(limit) if limit else 1000000),
            ).fetchall()
        except Exception:  # pragma: no cover
            return []
        return self._rows_to_dicts(reversed(rows))  # back to ascending seq

    def spans(self, since: int = 0, limit: int = 0) -> List[Dict[str, Any]]:
        return self._query("span", since, limit)

    def metrics(self, since: int = 0, limit: int = 0) -> List[Dict[str, Any]]:
        return self._query("metric", since, limit)

    def logs(self, since: int = 0, limit: int = 0) -> List[Dict[str, Any]]:
        return self._query("log", since, limit)

    def cursor(self) -> int:
        self.flush()
        try:
            r = self._conn().execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
            return int(r[0]) if r else 0
        except Exception:  # pragma: no cover
            return 0

    def stats(self) -> Dict[str, int]:
        self.flush()
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
            # attributes carry the model under gen_ai.request.model / llm.model_name
            where.append("data LIKE ?")
            args.append(f'%.model%": "%{model}%')
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
            where.append("status != 'ERROR'")
        if name:
            where.append("name LIKE ?")
            args.append(f"%{name}%")
        if kind:
            if kind in ("agent", "cron", "session", "subagent", "approval"):
                where.append("name LIKE ?")
                args.append(f"{kind}%")
            else:
                where.append("name LIKE ?")
                args.append(f"{kind}.%")
        if text:
            where.append("data LIKE ?")
            args.append(f"%{text}%")
        if trace_id:
            where.append("trace_id = ?")
            args.append(trace_id)
        return " AND ".join(where), args

    def spans_for_traces(self, trace_ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
        self.flush()
        """All stored spans of the given traces, grouped by trace id."""
        out: Dict[str, List[Dict[str, Any]]] = {t: [] for t in trace_ids}
        if not trace_ids:
            return out
        try:
            c = self._conn()
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
        except Exception:  # pragma: no cover
            pass
        return out

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
    ) -> Dict[str, Any]:
        self.flush()
        """Trace-list rows (newest first) whose spans match every given filter."""
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
        try:
            c = self._conn()
            total = int(
                c.execute(
                    f"SELECT COUNT(DISTINCT trace_id) FROM events WHERE {where}", args
                ).fetchone()[0]
            )
            ids = [
                r[0]
                for r in c.execute(
                    f"SELECT trace_id, MAX(COALESCE(end_ns, start_ns, ts)) AS last FROM events "
                    f"WHERE {where} GROUP BY trace_id ORDER BY last DESC LIMIT ? OFFSET ?",
                    args + [int(limit), int(offset)],
                ).fetchall()
            ]
        except Exception:  # pragma: no cover
            return {"traces": [], "total": 0}
        by_trace = self.spans_for_traces(ids)
        traces = [summarize_trace(by_trace[t]) for t in ids if by_trace.get(t)]
        return {"traces": traces, "total": total}

    def trace(self, trace_id: str) -> Dict[str, Any]:
        self.flush()
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
        self.flush()
        """One row per session id: turns, span/error counts, totals, first/last."""
        where, args = self._span_where(start_ns, end_ns, None, None, None, None, None)
        try:
            c = self._conn()
            ids = [
                r[0]
                for r in c.execute(
                    f"SELECT trace_id FROM events WHERE {where} AND session_id IS NOT NULL "
                    "GROUP BY trace_id ORDER BY MAX(COALESCE(end_ns, start_ns, ts)) DESC LIMIT ?",
                    args + [int(limit) * 20],
                ).fetchall()
            ]
        except Exception:  # pragma: no cover
            return {"sessions": []}
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
            row["toolCalls"] += sum(
                1 for sp in spans if str(sp.get("name", "")).startswith("tool.")
            )
            row["startNs"] = min(row["startNs"], s["startNs"])
            row["endNs"] = max(row["endNs"], s["endNs"])
            row["traceIds"].append(t)
        rows = sorted(sessions.values(), key=lambda r: r["endNs"], reverse=True)[: int(limit)]
        for r in rows:
            r["tokens"] = r["tokens"] or None
            r["cost"] = r["cost"] or None
        return {"sessions": rows}

    def metric_names(self) -> List[Dict[str, Any]]:
        self.flush()
        try:
            rows = (
                self._conn()
                .execute(
                    "SELECT name, COUNT(*), MAX(ts) FROM events WHERE kind='metric' GROUP BY name ORDER BY name"
                )
                .fetchall()
            )
        except Exception:  # pragma: no cover
            return []
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
        self.flush()
        """Aggregate one instrument into fixed time buckets, optionally per attribute value."""
        bucket_ns = max(1, int(bucket_s)) * 1_000_000_000
        start_ns = int(start_ns) - int(start_ns) % bucket_ns
        n_buckets = max(1, int((int(end_ns) - start_ns) // bucket_ns) + 1)
        try:
            rows = (
                self._conn()
                .execute(
                    "SELECT ts, value, data FROM events WHERE kind='metric' AND name=? AND ts>=? AND ts<=?",
                    (name, start_ns, int(end_ns)),
                )
                .fetchall()
            )
        except Exception:  # pragma: no cover
            rows = []
        series: Dict[str, List[List[float]]] = {}
        for ts, value, data in rows:
            if value is None:
                continue
            idx = int((int(ts) - start_ns) // bucket_ns)
            if idx < 0 or idx >= n_buckets:
                continue
            label = "_"
            if group_by:
                try:
                    label = str(json.loads(data).get("attributes", {}).get(group_by, "—"))
                except Exception:
                    label = "—"
            series.setdefault(label, [[] for _ in range(n_buckets)])[idx].append(float(value))

        def reduce(vals: List[float]) -> Optional[float]:
            if not vals:
                return None
            if agg == "count":
                return float(len(vals))
            if agg == "avg":
                return sum(vals) / len(vals)
            if agg == "max":
                return max(vals)
            if agg == "last":
                return vals[-1]
            return sum(vals)

        return {
            "name": name,
            "agg": agg,
            "bucketS": int(bucket_s),
            "buckets": [start_ns + i * bucket_ns for i in range(n_buckets)],
            "series": {label: [reduce(v) for v in vals] for label, vals in series.items()},
            "points": len(rows),
        }

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
    ) -> List[Dict[str, Any]]:
        self.flush()
        where = ["kind='log'"]
        args: List[Any] = []
        if trace_id:
            where.append("trace_id = ?")
            args.append(trace_id)
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
            where.append("data LIKE ?")
            args.append(f"%{text}%")
        if start_ns:
            where.append("ts >= ?")
            args.append(int(start_ns))
        if end_ns:
            where.append("ts <= ?")
            args.append(int(end_ns))
        try:
            rows = (
                self._conn()
                .execute(
                    f"SELECT seq, data FROM events WHERE {' AND '.join(where)} ORDER BY seq DESC LIMIT ?",
                    args + [int(limit)],
                )
                .fetchall()
            )
        except Exception:  # pragma: no cover
            return []
        return self._rows_to_dicts(rows)  # newest first

    def loggers(self) -> List[Dict[str, Any]]:
        self.flush()
        try:
            rows = (
                self._conn()
                .execute(
                    "SELECT logger, COUNT(*) FROM events WHERE kind='log' AND logger IS NOT NULL "
                    "GROUP BY logger ORDER BY COUNT(*) DESC"
                )
                .fetchall()
            )
        except Exception:  # pragma: no cover
            return []
        return [{"logger": n, "count": int(c)} for n, c in rows]


# Per-process singleton; both processes point at the same SQLite file (default
# path), so they share data without sharing memory.
_LIVE_STORE: Optional[LiveStore] = None
_LIVE_LOCK = threading.Lock()


def get_live_store(
    create: bool = False,
    db_path: Optional[str] = None,
    max_rows: int = 4000,
    retention_hours: float = 168.0,
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
                        retention_hours=retention_hours,
                    )
                except Exception:  # pragma: no cover
                    return None
    return _LIVE_STORE
