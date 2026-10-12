"""OpenObserve adapter — SQL over ``/api/{org}/_search?type=traces``.

OpenObserve stores traces as rows in a stream (default ``default``).
Attributes are flattened into columns with dots replaced by
underscores (``llm.model_name`` → ``llm_model_name``); the adapter maps them
back through a table of the plugin's known attribute names so the UI sees the
same keys it does on Tempo.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from . import register
from ._attrs import KNOWN_ATTRIBUTES, dotted, oo_col
from .base import (
    BackendAdapter,
    BackendError,
    ConfigError,
    HTTPException,
    LogFilter,
    StructuredFilter,
    bucketize,
    counter_increases,
    finish_log_row,
    http_get_json,
    http_post_json,
    otlp_attrs_from_dict,
    otlp_status,
    resolve_env_or_literal,
    rewrite_host_for_docker,
    strictly_older,
    strictly_older_traces,
    trace_page,
)

__all__ = ["OpenObserveAdapter", "HTTPException"]

_DEFAULT_OO_PORT = 5080
# The trace detail asks for this many rows and says so when there are more.
_DETAIL_SPAN_CAP = 500
# How far back a trace id is looked for when the detail view opens it: the
# list may have shown it from a 30-day lookback, so a week is not enough.
_DEFAULT_TRACE_WINDOW_DAYS = 90
# Raw metric samples loaded per chart; newest kept when there are more.
_METRIC_SAMPLE_CAP = 10000
# Python logging levels and the OTel spellings OpenObserve stores in ``severity``.
_LEVEL_NAMES = {
    10: ("DEBUG", "TRACE", "DEBUG2", "DEBUG3", "DEBUG4"),
    20: ("INFO", "INFO2", "INFO3", "INFO4"),
    30: ("WARN", "WARNING", "WARN2", "WARN3", "WARN4"),
    40: ("ERROR", "ERROR2", "ERROR3", "ERROR4"),
    50: ("FATAL", "CRITICAL", "FATAL2", "FATAL3", "FATAL4"),
}


def _severity_names_at_least(min_level: int) -> List[str]:
    """The ``severity`` spellings at or above a Python level (for a SQL ``IN``)."""
    names: List[str] = []
    for lvl in sorted(_LEVEL_NAMES):
        if lvl >= int(min_level):
            names.extend(_LEVEL_NAMES[lvl])
    return names


def _like_escape(v: Any) -> str:
    """A LIKE pattern fragment: SQL-quoted, with ``%`` and ``_`` literal."""
    return _sql_escape(v).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Columns the card renderer cares about. Expressed in Otel dot form;
# ``_oo_col`` translates to OpenObserve's underscore form on the wire.
_CARD_ATTR_KEYS = (
    "llm.model_name",
    "llm.provider",
    "llm.api_mode",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.total_tokens",
    "llm.response.finish_reason",
    "llm.response.tool_calls",
    "tool.name",
    "input.value",
    "output.value",
    "llm.output.content",
    "hermes.session_id",
    "hermes.turn.number",
)


_oo_col = oo_col


# The attribute table and the column<->name mapping live in ``_attrs`` (shared
# with the Loki and Uptrace adapters); the old names stay importable from here.
_KNOWN_ATTRIBUTES = KNOWN_ATTRIBUTES
_dotted = dotted


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
        parsed = _urlparse.urlparse(endpoint)
        host = parsed.hostname or "localhost"
        scheme = parsed.scheme or "http"
        port = cfg.get("query_port") or _DEFAULT_OO_PORT
        self.query_url = f"{scheme}://{rewrite_host_for_docker(host)}:{port}"
        # Org is the first path segment in the endpoint, e.g.
        # ``/api/default/v1/traces`` → ``default``.
        self.org = cfg.get("org") or _extract_org(endpoint) or "default"
        self.stream = cfg.get("stream_name") or cfg.get("stream") or "default"
        self.user = resolve_env_or_literal(cfg, "user", "user_env")
        self.password = resolve_env_or_literal(cfg, "password", "password_env")
        try:
            self.trace_window_days = int(cfg.get("trace_window_days") or _DEFAULT_TRACE_WINDOW_DAYS)
        except (TypeError, ValueError):
            self.trace_window_days = _DEFAULT_TRACE_WINDOW_DAYS

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        base["org"] = self.org
        base["stream"] = self.stream
        base["auth_required"] = not (self.user and self.password)
        return base

    def _headers(self) -> Dict[str, str]:
        if not (self.user and self.password):
            raise ConfigError(
                "OpenObserve requires basic auth credentials. Set "
                "user + password (or *_env) on the openobserve "
                "backend entry in config.yaml."
            )
        token = base64.b64encode(f"{self.user}:{self.password}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

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
            clauses.append(f"{_oo_col(k)} = '{_sql_escape(str(v))}'")
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
        end_us = int(end_s) * 1_000_000
        if f.before_ns:
            # The cursor is the end bound (µs, rounded up so the row it was
            # taken from stays inside); strictly_older_traces() cuts exactly.
            end_us = min(end_us, int(f.before_ns) // 1000 + 1)

        # One row beyond the page so has_more is exact. Without roots-only
        # several rows of one trace share a page, so ask for more and dedupe.
        want = int(limit) + 1
        wide = int(limit) * 4 + 1
        # Roots only: the stream names the parent in one of two columns
        # depending on the OpenObserve version. Each spelling is tried in
        # turn; a later attempt runs ONLY when the earlier one was rejected
        # (an unknown column is a 400), never when it matched nothing: an
        # empty answer is the answer (#299). The last attempt has no root
        # predicate and is filtered here from each row's parent fields.
        if f.roots_only:
            attempts = [
                (
                    f"SELECT * FROM {self.stream} WHERE {where} AND "
                    f"(reference_parent_span_id IS NULL OR reference_parent_span_id = '') "
                    f"ORDER BY _timestamp DESC LIMIT {want}",
                    want,
                    False,
                ),
                (
                    f"SELECT * FROM {self.stream} WHERE {where} AND "
                    f"(reference IS NULL OR reference = '') "
                    f"ORDER BY _timestamp DESC LIMIT {want}",
                    want,
                    False,
                ),
                (
                    f"SELECT * FROM {self.stream} WHERE {where} "
                    f"ORDER BY _timestamp DESC LIMIT {wide}",
                    wide,
                    True,
                ),
            ]
        else:
            attempts = [
                (
                    f"SELECT * FROM {self.stream} WHERE {where} "
                    f"ORDER BY _timestamp DESC LIMIT {wide}",
                    wide,
                    False,
                )
            ]

        rows: List[Dict[str, Any]] = []
        client_roots = False
        last_err: Optional[Exception] = None
        for sql, size, filter_roots_here in attempts:
            body = {
                "query": {
                    "sql": sql,
                    "start_time": int(start_s) * 1_000_000,  # µs
                    "end_time": end_us,
                    "size": size,
                }
            }
            try:
                data = http_post_json(url, body, headers=self._headers(), timeout=15.0)
            except BackendError as e:
                if e.kind != "backend" or "Backend returned 4" not in str(e.detail):
                    raise  # unreachable, auth, config: no other spelling will help
                last_err = e
                continue
            hits = data.get("hits") if isinstance(data, dict) else None
            rows = [h for h in (hits if isinstance(hits, list) else []) if isinstance(h, dict)]
            client_roots = filter_roots_here
            last_err = None
            break

        if last_err is not None:
            raise last_err

        traces: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            trace_id = row.get("trace_id") or row.get("traceId")
            if not trace_id or trace_id in traces:
                continue
            if client_roots and not self._is_root_row(row):
                continue
            start_ns = int(row.get("start_time") or 0)
            duration_us = int(row.get("duration") or 0)
            duration_ms = duration_us // 1000 if duration_us else 0

            attrs_map = _row_to_card_attrs(row)
            traces[trace_id] = {
                "traceID": trace_id,
                "rootServiceName": row.get("service_name") or "",
                "rootTraceName": row.get("operation_name") or row.get("name") or "",
                "startTimeUnixNano": str(start_ns) if start_ns else "0",
                "durationMs": duration_ms,
                "spanSets": [
                    {
                        "spans": [
                            {
                                "spanID": row.get("span_id"),
                                "name": row.get("operation_name") or row.get("name") or "",
                                "attributes": otlp_attrs_from_dict(attrs_map),
                            }
                        ],
                        "matched": 1,
                    }
                ],
            }
        page = trace_page(strictly_older_traces(list(traces.values()), f), limit)
        self._attach_span_counts({t["traceID"]: t for t in page["traces"]}, start_s, end_s)
        return page

    def _attach_span_counts(
        self, traces: Dict[str, Dict[str, Any]], start_s: int, end_s: int
    ) -> None:
        """Add ``spanCount`` (spans per trace) with one grouped query.

        The search query returns one row per trace, so the card would
        otherwise show "1 spans" for every trace (#179). A failed count
        query leaves ``spanCount`` unset; the UI then shows no number. The
        count's window is a day wider than the search on both sides so a
        trace straddling the window edge is not under-counted.
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
        # ``start_time`` is the counter's own start (ns): a series that started
        # inside the window is a fresh process whose first sample counts in
        # full (one-shot runs export each counter once, #299).
        window_start_ns = int(start_s) * 1_000_000_000
        started: set = set()
        for r in rows:
            v = _maybe_num(r.get("value"))
            if not isinstance(v, (int, float)):
                continue
            ident = "|".join(f"{k}={r[k]}" for k in sorted(r) if k not in skip)
            samples.append((int(r.get("_timestamp") or 0) * 1000, float(v), ident))
            st = _maybe_num(r.get("start_time"))
            if isinstance(st, (int, float)) and int(st) >= window_start_ns:
                started.add(ident)
        if cumulative:
            samples = counter_increases(samples, started_in_window=started)

        # Collapse the series identity to the requested group_by label.
        def label_of(ident: str) -> str:
            if not group_by:
                return "_"
            for part in ident.split("|"):
                k, _, val = part.partition("=")
                if k == group_by or k == group_by.replace(".", "_"):
                    return val
            return "—"

        points = [(ts, v, label_of(ident)) for ts, v, ident in samples]
        out = bucketize(
            points, int(start_s) * 1_000_000_000, int(end_s) * 1_000_000_000, bucket_s, agg
        )
        out["name"] = name
        out["cumulative"] = cumulative
        out["truncated"] = truncated
        return out

    # ── logs (#182) ──────────────────────────────────────────────────

    _LOG_STREAM_DEFAULT = "default"

    def _log_stream(self) -> str:
        return str(self.cfg.get("logs_stream") or self._LOG_STREAM_DEFAULT)

    @staticmethod
    def _level_name(row: Dict[str, Any]) -> str:
        sev = row.get("severity") or row.get("severity_text") or row.get("level") or "INFO"
        return str(sev).upper()

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
            names = ", ".join(f"'{n}'" for n in _severity_names_at_least(f.min_level))
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
        out: List[Dict[str, Any]] = []
        for r in rows:
            level = self._level_name(r)
            known = {
                "_timestamp",
                "body",
                "trace_id",
                "span_id",
                "session_id",
                "hermes_session_id",
                "severity",
                "severity_text",
                "severity_number",
                "level",
                "instrumentation_library_name",
                "logger_name",
                "event_name",
                "_p",
                "_o2_id",
            }
            row = {
                "level": level,
                "severity_number": r.get("severity_number"),
                # The OTLP logs exporter records the Python logger name as
                # the instrumentation scope; that is the column OpenObserve keeps.
                "logger": r.get("instrumentation_library_name") or r.get("logger_name") or "",
                "body": r.get("body") or "",
                "time_unix_nano": int(r.get("_timestamp") or 0) * 1000,
                "trace_id": r.get("trace_id"),
                "span_id": r.get("span_id"),
                "session_id": r.get("session_id") or r.get("hermes_session_id"),
                "event_name": r.get("event_name"),
            }
            # OpenObserve flattens attribute names (``hermes_log_attribution``);
            # give the documented ones their dotted name back (#268).
            out.append(finish_log_row(row, {_dotted(k): v for k, v in r.items() if k not in known}))
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
        the adapter queries, and a 30-day window ending an hour from now so
        the page's time picker contains the trace."""
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
            raise BackendError(
                404,
                f"Trace {trace_id} not found in OpenObserve stream {self.stream!r} "
                f"(last {self.trace_window_days} days)",
                "not_found",
            )
        out = _rows_to_otlp(rows)
        out["span_count"] = len(rows)
        out["truncated"] = len(rows) >= _DETAIL_SPAN_CAP
        return out


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


_CARD_ATTR_OO_COLS = tuple(_oo_col(k) for k in _CARD_ATTR_KEYS)


def _row_to_card_attrs(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": row.get("operation_name") or row.get("name") or ""}
    status_code = row.get("status_code")
    if status_code is not None:
        out["status"] = "error" if status_code == 2 else "ok"
    for dotted_key, col in zip(_CARD_ATTR_KEYS, _CARD_ATTR_OO_COLS):
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


def _rows_to_otlp(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_service: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        start_ns = int(row.get("start_time") or 0)
        end_ns = int(row.get("end_time") or 0)
        attrs: Dict[str, Any] = {}
        for k, v in row.items():
            if k in _SKIP_OTLP_COLS or v is None or v == "":
                continue
            attrs[_dotted(k)] = _maybe_num(v)

        parent = (
            row.get("reference_parent_span_id")
            or _extract_parent_from_reference(row.get("reference"))
            or row.get("parent_span_id")
        )
        otlp_span = {
            "traceId": row.get("trace_id"),
            "spanId": row.get("span_id"),
            "parentSpanId": parent or None,
            "name": row.get("operation_name") or row.get("name") or "",
            "kind": int(row.get("span_kind") or 1),
            "startTimeUnixNano": str(start_ns) if start_ns else "0",
            "endTimeUnixNano": str(end_ns) if end_ns else "0",
            "attributes": otlp_attrs_from_dict(attrs),
            "status": otlp_status(row.get("status_code"), row.get("status_message") or ""),
        }
        service = row.get("service_name") or ""
        by_service.setdefault(service, []).append(otlp_span)

    batches = []
    for service, spans in by_service.items():
        resource_attrs = otlp_attrs_from_dict({"service.name": service} if service else {})
        batches.append(
            {
                "resource": {"attributes": resource_attrs},
                "scopeSpans": [{"spans": spans}],
            }
        )
    return {"batches": batches}


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
