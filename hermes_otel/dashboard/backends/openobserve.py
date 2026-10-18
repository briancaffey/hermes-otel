"""OpenObserve adapter — SQL over ``/api/{org}/_search?type=traces``.

OpenObserve stores traces as rows in a stream (default ``default``).
Attributes are flattened into columns with dots replaced by
underscores (``llm.model_name`` → ``llm_model_name``); the adapter maps them
back through a table of the plugin's known attribute names so the UI sees the
same keys it does on Tempo.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from hermes_otel.severity import level_names_at_least

from . import register
from ._attrs import dotted, oo_col
from .base import (
    BackendAdapter,
    HTTPException,
    LogColumns,
    LogFilter,
    StructuredFilter,
    basic_auth,
    first_accepted,
    http_get_json,
    http_post_json,
    log_row,
    not_found,
    query_origin,
    require_credential,
    resolve_env_or_literal,
)
from .metrics_math import fold_samples, series_ident
from .paging import (
    strictly_older,
    strictly_older_traces,
    trace_end_bound,
    trace_page,
    widened_fetch,
)
from .shapes import (
    CARD_ATTRIBUTE_KEYS,
    SpanRow,
    card_row,
    otlp_kind,
    otlp_status,
    recard_roots,
    trace_detail,
)

# A widened search's root lookup without a server-side root filter keeps the
# roots from up to this many spans per trace (#558).
_ROOT_ROWS_PER_TRACE = 50


_DEFAULT_OO_PORT = 5080
# The trace detail asks for this many rows and says so when there are more.
_DETAIL_SPAN_CAP = 500
# How far back a trace id is looked for when the detail view opens it: the
# list may have shown it from a 30-day lookback, so a week is not enough.
_DEFAULT_TRACE_WINDOW_DAYS = 90
# Raw metric samples loaded per chart; newest kept when there are more.
_METRIC_SAMPLE_CAP = 10000
# A ``SELECT *`` row: the OTLP logs exporter records the Python logger name
# as the instrumentation scope, which is the column OpenObserve keeps; the
# flattened attribute names (``hermes_log_attribution``) get their dotted
# name back (#268). ``_p`` / ``_o2_id`` are OpenObserve's bookkeeping.
_LOG_COLUMNS = LogColumns(
    time="_timestamp",
    level=("severity", "severity_text", "level"),
    logger=("instrumentation_library_name", "logger_name"),
    session_id=("session_id", "hermes_session_id"),
    drop=("_p", "_o2_id"),
    attr_key=dotted,
)


def _like_escape(v: Any) -> str:
    """A LIKE pattern fragment: SQL-quoted, with ``%`` and ``_`` literal."""
    return _sql_escape(v).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _sql_escape(v: Any) -> str:
    return str(v).replace("\\", "\\\\").replace("'", "''")


def _is_missing_event_column(exc: Exception) -> bool:
    """True when OpenObserve refused the query because the logs stream has no
    ``event_name`` column yet (no event was ever ingested)."""
    text = str(getattr(exc, "detail", "") or exc)
    return "event_name" in text and (
        "No field named" in text or "unknown field" in text or "Search field not found" in text
    )


@register
class OpenObserveAdapter(BackendAdapter):
    handles = frozenset({"openobserve", "openobserver"})
    query_lang_label = "SQL WHERE"
    raw_placeholder = "llm_model_name = 'gpt-4'"
    # OpenObserve keeps every OTLP metric as its own stream (``type=metrics``)
    # and logs in a logs stream, both queryable with the same SQL API (#182).
    supports_metrics = True
    supports_logs = True
    filter_support = {
        "service": "server",
        "name": "server",
        "model": "server",
        "session": "server",
        "tool": "server",
        "min_duration": "server",
        "status_error": "server",
        "status_ok": "server",
        "free_text": "server",
        "raw": "server",
        "roots_only": "server",
    }

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        endpoint = cfg.get("endpoint") or ""
        self.query_url = query_origin(cfg, _DEFAULT_OO_PORT)
        # Org is the first path segment in the endpoint, e.g.
        # ``/api/default/v1/traces`` → ``default``; for a bare-origin
        # endpoint, the org the exporter completed it with (#582).
        self.org = (
            cfg.get("org")
            or _extract_org(endpoint)
            or os.getenv("OTEL_OPENOBSERVE_ORG", "").strip()
            or "default"
        )
        self.stream = cfg.get("stream_name") or cfg.get("stream") or "default"
        self.user = resolve_env_or_literal(cfg, "user", "user_env")
        self.password = resolve_env_or_literal(cfg, "password", "password_env")
        try:
            self.trace_window_days = int(cfg.get("trace_window_days") or _DEFAULT_TRACE_WINDOW_DAYS)
        except (TypeError, ValueError):
            self.trace_window_days = _DEFAULT_TRACE_WINDOW_DAYS

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["org"] = self.org
        base["stream"] = self.stream
        base["auth_required"] = not (self.user and self.password)
        return base

    def _headers(self) -> Dict[str, str]:
        require_credential(
            self.user and self.password,
            "OpenObserve requires basic auth credentials. Set "
            "user + password (or *_env) on the openobserve "
            "backend entry in hermes_otel.yaml.",
        )
        return basic_auth(self.user, self.password)

    # ── Query construction ───────────────────────────────────────────

    _TEXT_COLUMNS = (
        "gen_ai_input_messages",
        "gen_ai_output_messages",
        "input_value",
        "output_value",
        "llm_input",
        "llm_output_content",
        "tool_name",
        "operation_name",
        "status_message",
    )

    def _text_columns(self) -> List[str]:
        """The free-text search columns present in the traces stream's schema.

        Fetched once per adapter instance; when the schema cannot be read the
        two GenAI message columns are assumed (they exist on every api span).
        """
        cached = getattr(self, "_text_cols", None)
        if cached:
            return cached
        cols: List[str] = []
        try:
            url = f"{self.query_url}/api/{self.org}/streams/{self.stream}/schema?type=traces"
            data = http_get_json(url, headers=self._headers(), timeout=10.0)
            names = {f.get("name") for f in (data or {}).get("schema") or []}
            cols = [c for c in self._TEXT_COLUMNS if c in names]
        except Exception:
            cols = []
        self._text_cols = cols or ["gen_ai_input_messages", "gen_ai_output_messages"]
        return self._text_cols

    def _build_where(self, f: StructuredFilter) -> str:
        clauses: List[str] = []
        if f.service:
            clauses.append(f"service_name = '{_sql_escape(f.service)}'")
        if f.name_prefix:
            clauses.append(f"operation_name LIKE '{_like_escape(f.name_prefix)}%'")
        if f.name_regex:
            # OpenObserve SQL supports LIKE; the native text is a substring.
            clauses.append(f"operation_name LIKE '%{_like_escape(f.name_regex)}%'")
        if f.status == "error":
            clauses.append("status_code = 2")
        elif f.status == "ok":
            clauses.append("status_code = 1")
        if f.min_duration_ms:
            # duration stored in microseconds in OpenObserve traces stream.
            clauses.append(f"duration >= {int(f.min_duration_ms) * 1000}")
        for k, v in f.attr_equals.items():
            clauses.append(f"{oo_col(k)} = '{_sql_escape(str(v))}'")
        if f.free_text:
            # OpenObserve rejects a query naming a column the stream has not
            # seen (400 "unknown field"), so OR over the text columns that
            # exist: the GenAI message lists carry prompts and outputs, the
            # rest tool and status text.
            cols = self._text_columns()
            like = f"LIKE '%{_sql_escape(f.free_text)}%'"
            clauses.append("(" + " OR ".join(f"{c} {like}" for c in cols) + ")")

        raw = (f.raw or "").strip()
        if raw:
            clauses.append(f"({raw})")

        return " AND ".join(clauses) if clauses else "1=1"

    # ── Public API ───────────────────────────────────────────────────

    @staticmethod
    def _is_root_row(row: Dict[str, Any]) -> bool:
        return not (
            row.get("reference_parent_span_id")
            or _extract_parent_from_reference(row.get("reference"))
            or row.get("parent_span_id")
        )

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        where = self._build_where(f)
        url = f"{self.query_url}/api/{self.org}/_search?type=traces"
        # OpenObserve bounds time in µs: the cursor rounds up so its row stays inside.
        end_us = trace_end_bound(f, end_s, "us", round_up=True)
        # Without roots-only several rows of one trace share a page, so ask
        # for more and dedupe.
        want = widened_fetch(limit, True)
        wide = widened_fetch(limit, False)

        def rows_for(predicate: str, size: int) -> List[Dict[str, Any]]:
            body = {
                "query": {
                    "sql": f"SELECT * FROM {self.stream} WHERE {where}{predicate} "
                    f"ORDER BY _timestamp DESC LIMIT {size}",
                    "start_time": int(start_s) * 1_000_000,  # µs
                    "end_time": end_us,
                    "size": size,
                }
            }
            data = http_post_json(url, body, headers=self._headers(), timeout=15.0)
            hits = data.get("hits") if isinstance(data, dict) else None
            return [h for h in (hits if isinstance(hits, list) else []) if isinstance(h, dict)]

        # Roots only: the stream names the parent in one of two columns
        # depending on the OpenObserve version. Each spelling is tried in
        # turn; a later attempt runs ONLY when the earlier one was rejected
        # (an unknown column is a 400), never when it matched nothing: an
        # empty answer is the answer (#299). The last attempt has no root
        # predicate and is filtered here from each row's parent fields.
        client_roots = False
        if f.roots_only:
            rows, client_roots = first_accepted(
                lambda: (
                    rows_for(
                        " AND (reference_parent_span_id IS NULL OR reference_parent_span_id = '')",
                        want,
                    ),
                    False,
                ),
                lambda: (rows_for(" AND (reference IS NULL OR reference = '')", want), False),
                lambda: (rows_for("", wide), True),
            )
        else:
            rows = rows_for("", wide)

        traces: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            trace_id = row.get("trace_id") or row.get("traceId")
            if not trace_id or trace_id in traces:
                continue
            if client_roots and not self._is_root_row(row):
                continue
            traces[trace_id] = card_row(
                trace_id,
                span_id=row.get("span_id"),
                name=row.get("operation_name") or row.get("name") or "",
                attrs=_row_to_card_attrs(row),
                start_ns=int(row.get("start_time") or 0),
                # ``duration`` is in microseconds in the traces stream.
                duration_ms=int(row.get("duration") or 0) // 1000,
                service=row.get("service_name") or "",
                # The span count joins below, from one grouped query per page.
            )
        page = trace_page(strictly_older_traces(list(traces.values()), f), limit)
        if not f.roots_only and not f.name_prefix:
            # A tool filter widens to any span, but the row stands for the
            # trace: its root, the matched span its span set (#558).
            recard_roots(page, self._roots([t["traceID"] for t in page["traces"]], start_s, end_s))
        self._attach_span_counts({t["traceID"]: t for t in page["traces"]}, start_s, end_s)
        return page

    def _roots(self, trace_ids: List[str], start_s: int, end_s: int) -> Dict[str, SpanRow]:
        """The root spans of these traces, by trace id: one query on the
        ``trace_id IN`` list with the root predicate, each parent-column
        spelling tried in turn only when the previous one was rejected (as in
        ``search``), the last kept client-side from each row's parent fields.
        The window is a day wider on both sides, like the span count's. A
        failed query leaves the matched cards."""
        if not trace_ids:
            return {}
        ids = ", ".join(f"'{_sql_escape(t)}'" for t in trace_ids)
        url = f"{self.query_url}/api/{self.org}/_search?type=traces"

        def rows_for(predicate: str, size: int) -> List[Dict[str, Any]]:
            body = {
                "query": {
                    "sql": f"SELECT * FROM {self.stream} WHERE trace_id IN ({ids}){predicate} "
                    f"ORDER BY _timestamp ASC LIMIT {size}",
                    "start_time": max(0, int(start_s) - 86400) * 1_000_000,
                    "end_time": (int(end_s) + 86400) * 1_000_000,
                    "size": size,
                }
            }
            data = http_post_json(url, body, headers=self._headers(), timeout=15.0)
            hits = data.get("hits") if isinstance(data, dict) else None
            return [h for h in (hits if isinstance(hits, list) else []) if isinstance(h, dict)]

        n = len(trace_ids)
        try:
            rows = first_accepted(
                lambda: rows_for(
                    " AND (reference_parent_span_id IS NULL OR reference_parent_span_id = '')", n
                ),
                lambda: rows_for(" AND (reference IS NULL OR reference = '')", n),
                lambda: rows_for("", n * _ROOT_ROWS_PER_TRACE),
            )
        except Exception:
            return {}
        out: Dict[str, SpanRow] = {}
        for row in rows:
            if not self._is_root_row(row):
                continue
            tid = row.get("trace_id") or row.get("traceId")
            if not tid:
                continue
            start = int(row.get("start_time") or 0)
            out.setdefault(
                tid,
                SpanRow(
                    tid,
                    row.get("span_id"),
                    None,
                    str(row.get("operation_name") or row.get("name") or ""),
                    start_ns=start,
                    # ``duration`` is in microseconds in the traces stream.
                    end_ns=start + int(row.get("duration") or 0) * 1000,
                    attrs={},
                    service=str(row.get("service_name") or ""),
                ),
            )
        return out

    def _attach_span_counts(
        self, traces: Dict[str, Dict[str, Any]], start_s: int, end_s: int
    ) -> None:
        """Add ``spanCount`` (spans per trace) with one grouped query.

        The search query returns one row per trace, so the card would
        otherwise show "1 spans" for every trace (#179). A failed count
        query leaves ``spanCount`` unset; the UI then shows no number. The
        count's window is a day wider than the search on both sides so a
        trace straddling the window edge is not under-counted. ``matched``
        follows the count, as :func:`shapes.card_row` defines it.
        """
        if not traces:
            return
        ids = ", ".join(f"'{_sql_escape(t)}'" for t in traces)
        sql = (
            f"SELECT trace_id, COUNT(*) AS n FROM {self.stream} "
            f"WHERE trace_id IN ({ids}) GROUP BY trace_id"
        )
        body = {
            "query": {
                "sql": sql,
                "start_time": max(0, int(start_s) - 86400) * 1_000_000,
                "end_time": (int(end_s) + 86400) * 1_000_000,
                "size": len(traces),
            }
        }
        url = f"{self.query_url}/api/{self.org}/_search?type=traces"
        try:
            data = http_post_json(url, body, headers=self._headers(), timeout=15.0)
        except Exception:
            return
        hits = data.get("hits") if isinstance(data, dict) else None
        for row in hits or []:
            if not isinstance(row, dict):
                continue
            tid = row.get("trace_id")
            n = row.get("n")
            if tid in traces and isinstance(n, (int, float)) and n > 0:
                traces[tid]["spanCount"] = int(n)
                for span_set in traces[tid].get("spanSets") or []:
                    span_set["matched"] = int(n)

    # ── metrics (#182) ───────────────────────────────────────────────
    #
    # The plugin's exporter names streams ``hermes_<instrument>`` for its own
    # counters and ``gen_ai_client_token_usage`` etc. for the semconv ones.
    # Histograms arrive split into ``_sum`` / ``_count`` / ``_bucket`` streams;
    # counters arrive cumulative, so per-bucket values are increases between
    # consecutive samples of a series.

    def _search(
        self, sql: str, start_s: int, end_s: int, size: int, stream_type: str
    ) -> List[Dict[str, Any]]:
        url = f"{self.query_url}/api/{self.org}/_search?type={stream_type}"
        body = {
            "query": {
                "sql": sql,
                "start_time": int(start_s) * 1_000_000,
                "end_time": int(end_s) * 1_000_000,
                "size": int(size),
            }
        }
        data = http_post_json(url, body, headers=self._headers(), timeout=60.0)
        hits = data.get("hits") if isinstance(data, dict) else None
        return [h for h in hits or [] if isinstance(h, dict)]

    def metric_names(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        """Every metric stream (the window is not applied: a stream listing has
        no time bound). ``count`` is the stream's document count over its whole
        life, which ``count_scope`` says."""
        url = f"{self.query_url}/api/{self.org}/streams?type=metrics"
        data = http_get_json(url, headers=self._headers(), timeout=30.0)
        out: List[Dict[str, Any]] = []
        for s in (data.get("list") if isinstance(data, dict) else None) or []:
            name = s.get("name") if isinstance(s, dict) else None
            if not name or name.startswith("otel_sdk_"):
                continue
            if name.endswith(("_bucket", "_min", "_max")):
                continue  # histogram internals; _sum and _count stay
            docs = ((s.get("stats") or {}).get("doc_num")) if isinstance(s, dict) else None
            out.append({"name": name, "count": int(docs or 0), "count_scope": "stream"})
        return out

    def metrics_query(
        self,
        name: str,
        start_s: int,
        end_s: int,
        bucket_s: int,
        group_by: Optional[str] = None,
        agg: str = "sum",
    ) -> Dict[str, Any]:
        stream = name.replace('"', "")
        # Newest first so a window with more samples than the cap loses the
        # oldest, not the latest; the increases below need ascending order.
        rows = self._search(
            f'SELECT * FROM "{stream}" ORDER BY _timestamp DESC LIMIT {_METRIC_SAMPLE_CAP}',
            start_s,
            end_s,
            _METRIC_SAMPLE_CAP,
            "metrics",
        )
        truncated = len(rows) >= _METRIC_SAMPLE_CAP
        rows = sorted(rows, key=lambda r: int(r.get("_timestamp") or 0))
        # A monotonic cumulative sum is a counter; a histogram's ``_sum`` and
        # ``_count`` streams are cumulative too but OpenObserve stores them
        # without ``is_monotonic``.
        histogram_part = stream.endswith(("_sum", "_count"))
        cumulative = any(
            str(r.get("aggregation_temporality", "")).endswith("CUMULATIVE")
            and (str(r.get("is_monotonic")) == "true" or histogram_part)
            for r in rows
        )
        # Series identity is every label except the OTel/OpenObserve bookkeeping columns.
        skip = {
            "_timestamp",
            "value",
            "__hash__",
            "__name__",
            "aggregation_temporality",
            "flag",
            "is_monotonic",
            "start_time",
            "instrumentation_library_name",
            "instrumentation_library_version",
            "telemetry_sdk_language",
            "telemetry_sdk_name",
            "telemetry_sdk_version",
            "exemplars",
        }
        # ``service_instance_id`` / ``process_pid`` stay in the identity: a
        # counter is per process, and two processes' samples folded into one
        # series would read the second one's first value as "no increase".
        samples = []
        labels_of: Dict[str, str] = {}
        # ``start_time`` is the counter's own start (ns): a series that started
        # inside the window is a fresh process whose first sample counts in
        # full (one-shot runs export each counter once, #299).
        window_start_ns = int(start_s) * 1_000_000_000
        started: set = set()
        for r in rows:
            v = _maybe_num(r.get("value"))
            if not isinstance(v, (int, float)):
                continue
            ident = series_ident(r, drop=skip)
            # The requested label, in either spelling (``gen_ai.request.model``
            # arrives as ``gen_ai_request_model``).
            labels_of[ident] = (
                str(r.get(group_by, r.get(group_by.replace(".", "_"), "—"))) if group_by else "_"
            )
            samples.append((int(r.get("_timestamp") or 0) * 1000, float(v), ident))
            st = _maybe_num(r.get("start_time"))
            if isinstance(st, (int, float)) and int(st) >= window_start_ns:
                started.add(ident)
        out = fold_samples(
            samples,
            labels_of,
            window_start_ns,
            int(end_s) * 1_000_000_000,
            bucket_s,
            agg,
            name=name,
            cumulative=cumulative,
            started_in_window=started,
        )
        out.setdefault("cumulative", False)  # a gauge says so here, as detected
        out["truncated"] = truncated
        return out

    # ── logs (#182) ──────────────────────────────────────────────────

    _LOG_STREAM_DEFAULT = "default"

    def _log_stream(self) -> str:
        return str(self.cfg.get("logs_stream") or self._LOG_STREAM_DEFAULT)

    def logs_search(
        self, f: LogFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        where: List[str] = []
        if f.trace_id:
            where.append(f"trace_id = '{_sql_escape(f.trace_id)}'")
        if f.span_id:
            where.append(f"span_id = '{_sql_escape(f.span_id)}'")
        if f.min_level:
            # Severity in the query, not on the page: a page of DEBUG rows
            # would otherwise come back short of WARN lines that exist (#299).
            # OpenObserve stores the record's ``severity_text`` as ``severity``,
            # so the floor is the one severity table's spellings (#555).
            names = ", ".join(f"'{n}'" for n in level_names_at_least(f.min_level))
            where.append(f"severity IN ({names})")
        if f.session:
            # The exporter's ``hermes.session_id`` attribute; OpenObserve flattens
            # dots to underscores.
            where.append(f"hermes_session_id = '{_sql_escape(f.session)}'")
        if f.logger:
            where.append(f"instrumentation_library_name = '{_sql_escape(f.logger)}'")
        if f.text:
            where.append(f"body LIKE '%{_sql_escape(f.text)}%'")
        if f.event_name:
            where.append(f"event_name = '{_sql_escape(f.event_name)}'")
        elif f.events_only:
            where.append("event_name IS NOT NULL AND event_name != ''")
        wants_events = bool(f.event_name or f.events_only)
        if f.before_ns:
            where.append(f"_timestamp < {int(f.before_ns) // 1000}")  # µs column
        sql = f'SELECT * FROM "{self._log_stream()}"'
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY _timestamp DESC LIMIT {int(limit)}"
        try:
            rows = self._search(sql, start_s, end_s, int(limit), "logs")
        except HTTPException as exc:
            # OpenObserve rejects a WHERE on a column the stream has never seen
            # ("Search field not found … No field named event_name" on older
            # builds, ``{"code":20004,"message":"unknown field 'event_name'"}``
            # on current ones, seen 2026-10-09). Until an event has been
            # ingested there are no events to show: answer empty instead of
            # failing the whole Logs tab (#268, #299).
            if wants_events and _is_missing_event_column(exc):
                return []
            raise
        out = [
            log_row(r, _LOG_COLUMNS, time_ns=int(r.get("_timestamp") or 0) * 1000)  # µs column
            for r in rows
        ]
        return strictly_older(out, f)

    def loggers(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        rows = self._search(
            f'SELECT instrumentation_library_name AS logger, COUNT(*) AS n FROM "{self._log_stream()}" '
            "GROUP BY instrumentation_library_name ORDER BY n DESC LIMIT 200",
            start_s,
            end_s,
            200,
            "logs",
        )
        return [
            {"logger": r.get("logger") or "", "count": int(r.get("n") or 0)}
            for r in rows
            if r.get("logger")
        ]

    def trace_url(self, trace_id: str) -> Optional[str]:
        """The trace in OpenObserve's own UI (``/web/traces/trace-details``,
        verified in a browser against the 2026-10 image): the org and stream
        the adapter queries, and a 31-day window ending one to two hours from
        now so the page's time picker contains the trace."""
        base = str(self.cfg.get("ui_url") or self.query_url or "").rstrip("/")
        if not base:
            return None
        # Rounded up to the hour so the link is the same string for every
        # request in that hour (the page compares it with the API's).
        to_s = (int(time.time()) // 3600 + 2) * 3600
        params = {
            "org_identifier": self.org,
            "stream": self.stream,
            "trace_id": trace_id,
            "from": (to_s - 31 * 86400) * 1_000_000,
            "to": to_s * 1_000_000,
        }
        return f"{base}/web/traces/trace-details?{_urlparse.urlencode(params)}"

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        where = f"trace_id = '{_sql_escape(trace_id)}'"
        sql = (
            f"SELECT * FROM {self.stream} WHERE {where} "
            f"ORDER BY start_time ASC LIMIT {_DETAIL_SPAN_CAP}"
        )
        # OpenObserve requires a window; a trace id is unique, so it is as
        # wide as the list could have shown it from (``trace_window_days``).
        now = int(time.time())
        body = {
            "query": {
                "sql": sql,
                "start_time": max(0, now - self.trace_window_days * 86400) * 1_000_000,
                "end_time": (now + 3600) * 1_000_000,
                "size": _DETAIL_SPAN_CAP,
            }
        }
        url = f"{self.query_url}/api/{self.org}/_search?type=traces"
        data = http_post_json(url, body, headers=self._headers(), timeout=20.0)
        hits = data.get("hits") if isinstance(data, dict) else None
        rows = [h for h in (hits if isinstance(hits, list) else []) if isinstance(h, dict)]
        if not rows:
            raise not_found(
                trace_id,
                "OpenObserve",
                f"Trace {trace_id} not found in OpenObserve stream {self.stream!r} "
                f"(last {self.trace_window_days} days)",
            )
        return _rows_to_otlp(rows)


def _extract_org(endpoint: str) -> Optional[str]:
    try:
        parsed = _urlparse.urlparse(endpoint)
        # Path looks like ``/api/default/v1/traces``.
        parts = [p for p in (parsed.path or "").split("/") if p]
        if len(parts) >= 2 and parts[0] == "api":
            return parts[1]
    except Exception:
        pass
    return None


# The card attributes under the underscored column names OpenObserve stores.
_CARD_ATTR_OO_COLS = tuple(oo_col(k) for k in CARD_ATTRIBUTE_KEYS)


def _row_to_card_attrs(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": row.get("operation_name") or row.get("name") or ""}
    status_code = row.get("status_code")
    if status_code is not None:
        out["status"] = "error" if status_code == 2 else "ok"
    for dotted_key, col in zip(CARD_ATTRIBUTE_KEYS, _CARD_ATTR_OO_COLS):
        v = row.get(col)
        if v is not None and v != "":
            out[dotted_key] = _maybe_num(v)
    return out


def _maybe_num(v: Any) -> Any:
    """OpenObserve sometimes returns numeric columns as strings
    (``"13283"``). Pull those back to ints for the card renderer's
    token-count formatting."""
    if isinstance(v, str) and v.isdigit():
        try:
            return int(v)
        except Exception:
            return v
    return v


_SKIP_OTLP_COLS = frozenset(
    {
        "_timestamp",
        "trace_id",
        "span_id",
        "parent_span_id",
        "reference",
        "reference_parent_span_id",
        "reference_parent_trace_id",
        "reference_ref_type",
        "start_time",
        "end_time",
        "duration",
        "operation_name",
        "name",
        "service_name",
        "span_kind",
        "status_code",
        "status_message",
        "flags",
        "events",
        "links",
        "input_mime_type",
        "output_mime_type",
    }
)


def _row(row: Dict[str, Any]) -> SpanRow:
    """One stream row as a row: columns dotted back, the parent from
    whichever of the three places OpenObserve put it."""
    attrs = {
        dotted(k): _maybe_num(v)
        for k, v in row.items()
        if k not in _SKIP_OTLP_COLS and v is not None and v != ""
    }
    parent = (
        row.get("reference_parent_span_id")
        or _extract_parent_from_reference(row.get("reference"))
        or row.get("parent_span_id")
    )
    return SpanRow(
        row.get("trace_id"),
        row.get("span_id"),
        parent,
        str(row.get("operation_name") or row.get("name") or ""),
        start_ns=int(row.get("start_time") or 0),
        end_ns=int(row.get("end_time") or 0),
        attrs=attrs,
        service=str(row.get("service_name") or ""),
        kind=otlp_kind(row.get("span_kind")),
        status=otlp_status(row.get("status_code"), row.get("status_message") or ""),
    )


def _rows_to_otlp(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The detail every adapter answers (see :func:`trace_detail`), truncated
    when the rows fill ``_DETAIL_SPAN_CAP``."""
    return trace_detail(
        [_row(r) for r in rows if isinstance(r, dict)], truncated=len(rows) >= _DETAIL_SPAN_CAP
    )


def _extract_parent_from_reference(ref: Any) -> Optional[str]:
    """OpenObserve encodes parent-links as a JSON string in ``reference``.

    Example: ``[{"refType":"CHILD_OF","traceId":"…","spanId":"…"}]``.
    We return the first ``spanId`` we find.
    """
    if not ref:
        return None
    if isinstance(ref, list):
        for r in ref:
            if isinstance(r, dict) and r.get("spanId"):
                return r["spanId"]
    if isinstance(ref, str):
        try:
            parsed = json.loads(ref)
            if isinstance(parsed, list):
                for r in parsed:
                    if isinstance(r, dict) and r.get("spanId"):
                        return r["spanId"]
        except Exception:
            pass
    return None
