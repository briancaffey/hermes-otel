"""GreptimeDB adapter — traces through its Jaeger-compatible API, metrics and
logs through SQL over HTTP (#363).

GreptimeDB stores the three signals as tables and reads them back three ways:

* Traces: the Jaeger v1 JSON API under ``/v1/jaeger`` (``/api/services``,
  ``/api/traces?service=…``, ``/api/traces/{id}``), typed tags, ``CHILD_OF``
  references, one process per resource. Verified on v1.2.1 with ``start`` /
  ``end`` in µs, ``tags`` as JSON, ``operation`` exact and ``minDuration`` with
  a unit, the grammar the Jaeger adapter already speaks, so this adapter is
  that one with the API mounted under a prefix.
* Metrics: one table per metric in Prometheus naming
  (``hermes_token_usage_total``, ``…_duration_seconds_sum`` / ``_count`` /
  ``_bucket``), ``greptime_timestamp`` (ms) + ``greptime_value`` and one TAG
  column per label with dots turned to underscores. GreptimeDB's PromQL
  endpoint answers a range-vector instant query (``name[3600s]``) with an
  empty body, so the raw cumulative samples the counter maths needs (#296)
  come from SQL instead; the metric list uses PromQL's label-values route.
* Logs: ``opentelemetry_logs`` (``timestamp`` ns, ``severity_*``, ``body``,
  ``scope_name``, ``trace_id``, ``span_id``, ``log_attributes`` JSON). Dotted
  attribute keys are reached with the bracket path
  ``json_get_string(log_attributes, '["hermes.session_id"]')``.

SQL goes to ``POST /v1/sql?db=<db>`` as a form body; Basic auth from ``user``
/ ``password`` (or their ``*_env``) when the server runs a user provider.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib import parse as _urlparse
from urllib import request as _urlrequest

from hermes_otel.severity import otel_severity

from . import register
from ._prometheus import PrometheusMetrics, is_counter
from .base import (
    BackendError,
    LogColumns,
    LogFilter,
    _execute,
    basic_auth,
    bearer,
    log_row,
    register_cache,
    resolve_env_or_literal,
    validate_label_name,
    validate_metric_name,
)
from .jaeger import JaegerAdapter
from .metrics_math import LOOKBEHIND_S, bucketize, fold_samples, series_ident
from .paging import LOG_PAGE_SLACK, log_end_ns, strictly_older
from .shapes import to_int

# The logs table's columns; session and event live in ``log_attributes``.
_LOG_COLUMNS = LogColumns(
    time="timestamp",
    level="severity_text",
    logger="scope_name",
    session_id="hermes.session_id",
    event_name="event.name",
)

_DEFAULT_GREPTIME_PORT = 4000
# ``/api/traces?limit=N`` keeps N arbitrary traces, not the newest (verified on
# v1.2.1: limit=2 dropped the newest of five), so a page over-fetches and is cut
# here, the way the Tempo adapter does.
_DEFAULT_SEARCH_FETCH = 500
_DEFAULT_LOGS_TABLE = "opentelemetry_logs"
_DEFAULT_DB = "public"
_METRIC_TIME_COL = "greptime_timestamp"
_METRIC_VALUE_COL = "greptime_value"
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:]*$")

# Table columns per metric, fetched once per adapter instance (the dashboard
# polls; the schema only grows when a new label appears).
_COLUMNS_CACHE: Dict[Tuple[str, str], List[str]] = register_cache({})


def sql_str(value: Any) -> str:
    """A SQL string literal: single quotes doubled, backslashes doubled."""
    text = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{text}'"


def sql_like(value: str) -> str:
    """A ``LIKE '%…%'`` literal with the wildcard characters escaped."""
    text = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    text = text.replace("'", "''")
    return f"'%{text}%'"


def sql_ident(name: str) -> str:
    """A double-quoted identifier; anything outside the metric / label grammar
    is refused before it reaches the query text."""
    if not _IDENT_RE.match(name or "") or '"' in name:
        raise BackendError(400, f"Invalid identifier {name!r}", "request")
    return f'"{name}"'


def json_key(key: str) -> str:
    """The ``json_get_string`` path for one top-level key, dotted or not:
    ``["hermes.session_id"]`` (a bare dotted path would descend)."""
    return "'" + json.dumps([str(key)]).replace("'", "''") + "'"


@register
class GreptimeAdapter(PrometheusMetrics, JaegerAdapter):
    handles = frozenset({"greptime"})
    query_lang_label = "Jaeger tags (key=value)"
    raw_placeholder = "tool.name=terminal error=true"
    default_query_port = _DEFAULT_GREPTIME_PORT
    default_query_path = "/v1/jaeger"
    default_search_fetch = _DEFAULT_SEARCH_FETCH
    supports_metrics = True
    supports_logs = True

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        # The Jaeger-compatible API has no v3; never probe for one.
        self.query_api = "v1"
        self.db = str(cfg.get("db") or _DEFAULT_DB)
        self.logs_table = str(cfg.get("logs_table") or _DEFAULT_LOGS_TABLE)
        self.user = resolve_env_or_literal(cfg, "user", "user_env")
        self.password = resolve_env_or_literal(cfg, "password", "password_env")
        self.sql_url = f"{self.query_url}/v1/sql?{_urlparse.urlencode({'db': self.db})}"
        # The metric list comes from the PromQL label-values route; the
        # samples from SQL (``metrics_query`` below).
        self._use_prometheus(f"{self.query_url}/v1/prometheus", cfg)

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_api"] = "jaeger-compatible"
        base["sql_url"] = self.sql_url
        base["prometheus_url"] = self.prometheus_url
        base["db"] = self.db
        return base

    def _headers(self) -> Dict[str, str]:
        if self.user and self.password:
            return basic_auth(self.user, self.password)
        return bearer(self.api_key)

    def trace_url(self, trace_id: str) -> Optional[str]:
        # The embedded dashboard has a Traces view but no documented deep link
        # to one trace; ``ui_url`` on the entry is the way to offer one.
        return None

    # ── SQL over HTTP ─────────────────────────────────────────────────

    def _sql(self, sql: str, timeout: float = 30.0) -> Tuple[List[str], List[List[Any]]]:
        """``(columns, rows)`` of one statement; a GreptimeDB error becomes a
        ``backend`` error carrying the server's message."""
        body = _urlparse.urlencode({"sql": sql}).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            **self._headers(),
        }
        req = _urlrequest.Request(self.sql_url, data=body, headers=headers, method="POST")
        data = _execute(req, timeout)
        if not isinstance(data, dict):
            raise BackendError(502, "GreptimeDB returned an unexpected SQL answer")
        if data.get("error"):
            raise BackendError(502, f"GreptimeDB SQL error: {str(data['error'])[:300]}")
        outputs = data.get("output") or []
        records = (outputs[0] or {}).get("records") if outputs else None
        if not isinstance(records, dict):
            return [], []
        cols = [c.get("name") for c in (records.get("schema") or {}).get("column_schemas") or []]
        rows = records.get("rows") or []
        return [str(c) for c in cols], [r for r in rows if isinstance(r, list)]

    def _columns(self, table: str) -> List[str]:
        key = (self.sql_url, table)
        cached = _COLUMNS_CACHE.get(key)
        if cached:
            return cached
        _, rows = self._sql(
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema = {sql_str(self.db)} AND table_name = {sql_str(table)}"
        )
        cols = [str(r[0]) for r in rows if r and r[0]]
        if cols:
            _COLUMNS_CACHE[key] = cols
        return cols

    # ── metrics (#194 shapes) ─────────────────────────────────────────

    def metrics_query(
        self,
        name: str,
        start_s: int,
        end_s: int,
        bucket_s: int,
        group_by: Optional[str] = None,
        agg: str = "sum",
    ) -> Dict[str, Any]:
        validate_metric_name(name)
        label = validate_label_name(group_by)
        label_col = label.replace(".", "_") if label else None
        start_ns, end_ns = int(start_s) * 1_000_000_000, int(end_s) * 1_000_000_000
        counter = is_counter(name)
        columns = self._columns(name)
        if not columns:
            out = bucketize([], start_ns, end_ns, bucket_s, agg)
            out.update({"name": name, "agg": agg, "agg_requested": agg, "sql": None})
            return out
        tag_cols = [c for c in columns if c not in (_METRIC_TIME_COL, _METRIC_VALUE_COL)]
        if label_col and label_col not in tag_cols:
            label_col = None
        # Counters need the samples just before the window to tell a new
        # series (its first value counts) from a running one (#296).
        from_ms = (int(start_s) - (LOOKBEHIND_S if counter else 0)) * 1000
        select = ", ".join(sql_ident(c) for c in [_METRIC_TIME_COL, _METRIC_VALUE_COL, *tag_cols])
        sql = (
            f"SELECT {select} FROM {sql_ident(name)} "
            f"WHERE {sql_ident(_METRIC_TIME_COL)} >= {from_ms}::TIMESTAMP_MS "
            f"AND {sql_ident(_METRIC_TIME_COL)} < {int(end_s) * 1000}::TIMESTAMP_MS "
            f"ORDER BY {sql_ident(_METRIC_TIME_COL)}"
        )
        cols, rows = self._sql(sql)
        idx = {c: i for i, c in enumerate(cols)}
        samples: List[Tuple[int, float, str]] = []
        labels_of: Dict[str, str] = {}
        for row in rows:
            try:
                ts_ns = int(row[idx[_METRIC_TIME_COL]]) * 1_000_000
                value = float(row[idx[_METRIC_VALUE_COL]])
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            tags = {c: row[idx[c]] for c in tag_cols if c in idx and row[idx[c]] is not None}
            ident = series_ident(tags)
            labels_of[ident] = str(tags.get(label_col, "—")) if label_col else "_"
            samples.append((ts_ns, value, ident))
        out = fold_samples(
            samples, labels_of, start_ns, end_ns, bucket_s, agg, name=name, cumulative=counter
        )
        out["sql"] = sql
        return out

    # ── logs (#268 shapes) ────────────────────────────────────────────

    def _log_predicates(self, f: LogFilter, start_s: int, end_s: int) -> List[str]:
        preds = [
            f'"timestamp" >= {int(start_s) * 1_000_000_000}::TIMESTAMP_NS',
            f'"timestamp" < {log_end_ns(end_s, f)}::TIMESTAMP_NS',
        ]
        if f.trace_id:
            preds.append(f'"trace_id" = {sql_str(f.trace_id)}')
        if f.span_id:
            preds.append(f'"span_id" = {sql_str(f.span_id)}')
        if f.session:
            preds.append(
                f'json_get_string("log_attributes", {json_key("hermes.session_id")}) '
                f"= {sql_str(f.session)}"
            )
        if f.logger:
            preds.append(f'"scope_name" = {sql_str(f.logger)}')
        if f.min_level:
            preds.append(f'"severity_number" >= {otel_severity(f.min_level)}')
        if f.event_name:
            preds.append(
                f'json_get_string("log_attributes", {json_key("event.name")}) '
                f"= {sql_str(f.event_name)}"
            )
        elif f.events_only:
            preds.append(f'json_get_string("log_attributes", {json_key("event.name")}) IS NOT NULL')
        if f.text:
            preds.append(f'"body" LIKE {sql_like(f.text)}')
        return preds

    def logs_search(
        self, f: LogFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        n = int(limit) + LOG_PAGE_SLACK
        sql = (
            'SELECT "timestamp", "severity_text", "severity_number", "scope_name", "body", '
            '"trace_id", "span_id", "log_attributes" '
            f"FROM {sql_ident(self.logs_table)} WHERE "
            + " AND ".join(self._log_predicates(f, start_s, end_s))
            + f' ORDER BY "timestamp" DESC LIMIT {n}'
        )
        cols, rows = self._sql(sql)
        records: List[Dict[str, Any]] = []
        for row in rows:
            rec = dict(zip(cols, row))
            attrs = rec.pop("log_attributes", None)
            if isinstance(attrs, str):
                try:
                    attrs = json.loads(attrs)
                except (TypeError, ValueError):
                    attrs = {"raw": attrs}
            raw = {**(attrs if isinstance(attrs, dict) else {}), **rec}
            records.append(log_row(raw, _LOG_COLUMNS, time_ns=to_int(rec.get("timestamp"))))
        return strictly_older(records, f)

    def loggers(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        sql = (
            f'SELECT "scope_name", count(*) AS n FROM {sql_ident(self.logs_table)} '
            f'WHERE "timestamp" >= {int(start_s) * 1_000_000_000}::TIMESTAMP_NS '
            f'AND "timestamp" < {int(end_s) * 1_000_000_000}::TIMESTAMP_NS '
            'GROUP BY "scope_name" ORDER BY n DESC LIMIT 200'
        )
        _, rows = self._sql(sql)
        out = []
        for row in rows:
            if row and row[0]:
                try:
                    out.append({"logger": str(row[0]), "count": int(row[1])})
                except (TypeError, ValueError, IndexError):
                    continue
        return out
