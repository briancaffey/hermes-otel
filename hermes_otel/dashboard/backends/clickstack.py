"""ClickStack / HyperDX adapter — SQL over ClickHouse's HTTP interface (#365).

ClickStack's bundled Collector writes the standard OTel ClickHouse-exporter
tables: ``otel_traces`` (one row per span, ``SpanAttributes`` and
``ResourceAttributes`` as ``Map(String, String)``, ``Events.*`` arrays,
``Duration`` in ns, ``StatusCode`` ``Ok`` / ``Error`` / ``Unset``),
``otel_logs`` (``Body``, ``SeverityText`` lower-cased by the Collector,
``LogAttributes`` map, ``TraceId`` / ``SpanId``) and one table per metric type
(``otel_metrics_sum`` / ``_gauge`` / ``_histogram``) keyed by the **dotted**
``MetricName`` with ``TimeUnix`` in seconds. The HyperDX API would need a
second, personal key and a source lookup for the same rows, so the adapter
reads ClickHouse directly (verified on the all-in-one 2.40.0 image, 2026-10-11).

Every attribute value is a string in these maps; numeric and boolean values
are coerced back on read, except on id-like keys, so token counts render as
numbers and session ids stay strings. Histograms are listed as
``<name>.sum`` and ``<name>.count`` so the dashboard's curated panels find
them through :func:`otlp_metric_name`; counters (``otel_metrics_sum``) and
those histogram columns are cumulative and become per-bucket increases with
first-sample counting (#296), gauges stay raw.

Connection: ``query_port`` (default 8123) on the endpoint's host, ``database``
(default ``default``), Basic auth from ``user`` / ``password`` (the all-in-one
image's ``default`` user only answers from inside the container; its ``api`` /
``api`` user answers from the host). ``raw`` is a SQL predicate ANDed into the
``WHERE`` clause, the way the OpenObserve adapter takes it.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib import parse as _urlparse
from urllib import request as _urlrequest

from . import default_service_name, register
from .base import (
    BackendAdapter,
    BackendError,
    LogColumns,
    LogFilter,
    StructuredFilter,
    _execute,
    basic_auth,
    log_row,
    not_found,
    otlp_status,
    query_origin,
    resolve_env_or_literal,
    validate_label_name,
    validate_metric_name,
)
from .metrics_math import bucketize, counter_increases
from .paging import (
    LOG_PAGE_SLACK,
    log_end_ns,
    mark_full_fetch,
    strictly_older,
    strictly_older_traces,
    trace_end_bound,
    trace_page,
    widened_fetch,
)
from .severity import otel_severity_for
from .shapes import SpanRow, otlp_event, otlp_kind, to_int, trace_detail
from .zipkin import coerce_tag

_DEFAULT_CLICKHOUSE_PORT = 8123
_DEFAULT_DATABASE = "default"
_DEFAULT_METRICS_MATCH = r"^(hermes|gen_ai|process|system|hw)[._]"
# Counters need the samples just before the window to tell a new series (its
# first value counts) from a running one (#296); same look-behind as Prometheus.
LOOKBEHIND_S = 300
# A widened search (kind / tool filter) is built from span rows; several spans
# of one trace share a page, so fetch a few rows per requested trace.
_ROWS_PER_TRACE = 4

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sql_str(value: Any) -> str:
    """A ClickHouse string literal: backslashes and single quotes escaped."""
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


def sql_like(value: str) -> str:
    """A ``LIKE '%…%'`` literal with the wildcard characters escaped."""
    text = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "'%" + text.replace("'", "\\'") + "%'"


def sql_ident(name: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise BackendError(400, f"Invalid identifier {name!r}", "request")
    return f"`{name}`"


# The ``otel_logs`` columns the search selects; session and event live in
# the ``LogAttributes`` map, which is merged into the row before it is read.
_LOG_COLUMNS = LogColumns(
    body="Body",
    time="ts_ns",
    level="SeverityText",
    severity_number="SeverityNumber",
    logger="ScopeName",
    trace_id="TraceId",
    span_id="SpanId",
    session_id="hermes.session_id",
    event_name="event.name",
    drop=("LogAttributes",),
)


def _coerce_map(attrs: Any) -> Dict[str, Any]:
    if not isinstance(attrs, dict):
        return {}
    return {str(k): coerce_tag(str(k), v) for k, v in attrs.items()}


def _status_from(code: Any, message: Any) -> Dict[str, Any]:
    text = str(code or "").strip().lower()
    if text == "error":
        return otlp_status("error", str(message or ""))
    if text == "ok":
        return otlp_status("ok", str(message or ""))
    return {"code": 0, "message": str(message or "")}


def _row(row: Dict[str, Any], trace_id: Any = None) -> SpanRow:
    """One ``otel_traces`` row as a row: the maps coerced, ``Duration`` in ns,
    the ``Events.*`` arrays zipped back into events. The resource is the whole
    attribute map (plus ``service.name`` when the map lacks it), so two
    processes of one service stay apart in the detail."""
    start_ns = to_int(row.get("ts_ns"))
    times = row.get("event_ts") or []
    attrs_list = row.get("event_attrs") or []
    resource = _coerce_map(row.get("ResourceAttributes"))
    service = row.get("ServiceName") or ""
    if service and "service.name" not in resource:
        resource["service.name"] = service
    return SpanRow(
        row.get("TraceId") or trace_id,
        row.get("SpanId"),
        row.get("ParentSpanId"),
        str(row.get("SpanName") or ""),
        start_ns=start_ns,
        end_ns=start_ns + to_int(row.get("Duration")) if start_ns else 0,
        attrs=_coerce_map(row.get("SpanAttributes")),
        service=str(service),
        kind=otlp_kind(row.get("SpanKind")),
        status=_status_from(row.get("StatusCode"), row.get("StatusMessage")),
        events=[
            otlp_event(
                str(name),
                to_int(times[i]) if i < len(times) else 0,
                _coerce_map(attrs_list[i]) if i < len(attrs_list) else {},
            )
            for i, name in enumerate(row.get("event_names") or [])
        ],
        resource=resource,
    )


@register
class ClickstackAdapter(BackendAdapter):
    handles = frozenset({"clickstack", "hyperdx"})
    query_lang_label = "ClickHouse SQL predicate"
    raw_placeholder = "SpanAttributes['tool.name'] = 'terminal'"
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
        self.query_url = query_origin(cfg, _DEFAULT_CLICKHOUSE_PORT)
        self.database = str(cfg.get("database") or _DEFAULT_DATABASE)
        self.user = resolve_env_or_literal(cfg, "user", "user_env")
        self.password = resolve_env_or_literal(cfg, "password", "password_env")
        self.default_service = default_service_name(cfg)
        self.traces_table = str(cfg.get("traces_table") or "otel_traces")
        self.logs_table = str(cfg.get("logs_table") or "otel_logs")
        try:
            self.metrics_match = re.compile(cfg.get("metrics_match") or _DEFAULT_METRICS_MATCH)
        except re.error as exc:
            raise BackendError(400, f"Invalid metrics_match: {exc}", "request")
        self.ui_url = (cfg.get("ui_url") or "").strip().rstrip("/") or None

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["database"] = self.database
        base["default_service"] = self.default_service
        return base

    # ── ClickHouse HTTP ───────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        hdr = {"Content-Type": "text/plain; charset=utf-8"}
        if self.user is not None:
            hdr.update(basic_auth(self.user, self.password))
        return hdr

    def _sql(self, sql: str, timeout: float = 30.0) -> List[Dict[str, Any]]:
        """Rows of one statement as dicts (``FORMAT JSON``); 64-bit integers
        unquoted so counts and durations arrive as numbers. ClickHouse answers
        a bad query with 400/404 and a text body, neither of which is "no such
        trace", and a bad password with 516 as well as 401/403."""
        params = {
            "database": self.database,
            "default_format": "JSON",
            "output_format_json_quote_64bit_integers": "0",
        }
        url = f"{self.query_url}/?{_urlparse.urlencode(params)}"
        req = _urlrequest.Request(
            url, data=sql.encode("utf-8"), headers=self._headers(), method="POST"
        )
        data = _execute(
            req,
            timeout,
            label="ClickHouse",
            auth_codes=(401, 403, 516),
            not_found_is_backend=True,
        )
        rows = data.get("data") if isinstance(data, dict) else None
        return [r for r in (rows or []) if isinstance(r, dict)]

    def _table(self, name: str) -> str:
        return f"{sql_ident(self.database)}.{sql_ident(name)}"

    # ── traces ────────────────────────────────────────────────────────

    def _span_predicates(self, f: StructuredFilter, start_s: int, end_s: int) -> List[str]:
        preds = [
            f"Timestamp >= fromUnixTimestamp64Nano({int(start_s) * 1_000_000_000})",
            f"Timestamp < fromUnixTimestamp64Nano({trace_end_bound(f, end_s, 'ns')})",
            f"ServiceName = {sql_str(f.service or self.default_service)}",
        ]
        if f.roots_only:
            preds.append("ParentSpanId = ''")
        if f.name_regex:
            preds.append(f"SpanName = {sql_str(f.name_regex)}")
        if f.name_prefix:
            preds.append(f"startsWith(SpanName, {sql_str(f.name_prefix)})")
        for k, v in f.attr_equals.items():
            preds.append(f"SpanAttributes[{sql_str(k)}] = {sql_str(v)}")
        if f.min_duration_ms and f.min_duration_ms > 0:
            preds.append(f"Duration >= {int(f.min_duration_ms) * 1_000_000}")
        if f.status == "error":
            preds.append("StatusCode = 'Error'")
        elif f.status == "ok":
            preds.append("StatusCode != 'Error'")
        if f.free_text:
            like = sql_like(f.free_text)
            preds.append(
                f"(SpanAttributes['input.value'] ILIKE {like} "
                f"OR SpanAttributes['output.value'] ILIKE {like})"
            )
        if f.raw and f.raw.strip():
            preds.append(f"({f.raw.strip()})")
        return preds

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        fetch = widened_fetch(limit, f.roots_only, _ROWS_PER_TRACE, inclusive=True)
        sql = (
            "SELECT TraceId, SpanId, SpanName, ServiceName, StatusCode, Duration, SpanAttributes, "
            "toUnixTimestamp64Nano(Timestamp) AS ts_ns "
            f"FROM {self._table(self.traces_table)} WHERE "
            + " AND ".join(self._span_predicates(f, start_s, end_s))
            + f" ORDER BY Timestamp DESC LIMIT {fetch}"
        )
        rows = self._sql(sql)
        cards: Dict[str, SpanRow] = {}
        for row in rows:
            r = _row(row)
            tid = str(r.trace_id or "")
            if not tid:
                continue
            # Several spans of one trace may come back on a widened search; the
            # card is the earliest matching span, as the other adapters show it.
            prev = cards.get(tid)
            if prev is None or r.start_ns < prev.start_ns:
                cards[tid] = r
        trace_ids = list(cards)
        counts: Dict[str, int] = {}
        if trace_ids:
            in_list = ", ".join(sql_str(t) for t in trace_ids)
            for r in self._sql(
                f"SELECT TraceId, count() AS n FROM {self._table(self.traces_table)} "
                f"WHERE TraceId IN ({in_list}) GROUP BY TraceId"
            ):
                counts[str(r.get("TraceId"))] = to_int(r.get("n"))
        traces = [r.card(counts.get(tid, 1)) for tid, r in cards.items()]
        page = trace_page(strictly_older_traces(traces, f), limit)
        return mark_full_fetch(page, len(rows), fetch, widened=not f.roots_only)

    def trace_url(self, trace_id: str) -> Optional[str]:
        # HyperDX's search page takes a trace id in its query; only offered
        # when the entry names the UI (the UI port is not the ClickHouse port).
        if not self.ui_url:
            return None
        return f"{self.ui_url}/search?{_urlparse.urlencode({'q': f'TraceId:{trace_id}'})}"

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        sql = (
            "SELECT TraceId, SpanId, ParentSpanId, SpanName, SpanKind, ServiceName, StatusCode, "
            "StatusMessage, Duration, SpanAttributes, ResourceAttributes, "
            "toUnixTimestamp64Nano(Timestamp) AS ts_ns, "
            "arrayMap(t -> toUnixTimestamp64Nano(t), Events.Timestamp) AS event_ts, "
            "Events.Name AS event_names, Events.Attributes AS event_attrs "
            f"FROM {self._table(self.traces_table)} WHERE TraceId = {sql_str(trace_id)} "
            "ORDER BY Timestamp"
        )
        rows = self._sql(sql)
        if not rows:
            raise not_found(trace_id, "ClickStack")
        return trace_detail([_row(row, trace_id) for row in rows])

    # ── metrics (#194 shapes) ─────────────────────────────────────────

    def _metric_window(self, start_s: int, end_s: int, lookbehind_s: int = 0) -> str:
        return (
            f"TimeUnix >= toDateTime({int(start_s) - int(lookbehind_s)}) "
            f"AND TimeUnix < toDateTime({int(end_s)})"
        )

    def metric_names(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        window = self._metric_window(start_s, end_s)
        sql = (
            f"SELECT 'sum' AS kind, MetricName FROM {self._table('otel_metrics_sum')} "
            f"WHERE {window} GROUP BY MetricName "
            f"UNION ALL SELECT 'gauge', MetricName FROM {self._table('otel_metrics_gauge')} "
            f"WHERE {window} GROUP BY MetricName "
            f"UNION ALL SELECT 'histogram', MetricName FROM {self._table('otel_metrics_histogram')} "
            f"WHERE {window} GROUP BY MetricName"
        )
        names: List[str] = []
        for r in self._sql(sql):
            name = str(r.get("MetricName") or "")
            if not name or not self.metrics_match.search(name):
                continue
            if r.get("kind") == "histogram":
                names.extend([f"{name}.sum", f"{name}.count"])
            else:
                names.append(name)
        return [{"name": n} for n in sorted(set(names))]

    def _samples(
        self, name: str, start_s: int, end_s: int, lookbehind_s: int
    ) -> Tuple[List[Dict[str, Any]], str, bool]:
        """``(rows, value_column, cumulative)`` for one dashboard metric name:
        ``<hist>.sum`` / ``<hist>.count`` read the histogram table, anything
        else the sum table first, then the gauge table."""
        window = self._metric_window(start_s, end_s, lookbehind_s)
        base, _, suffix = name.rpartition(".")
        if suffix in ("sum", "count") and base:
            col = "Sum" if suffix == "sum" else "Count"
            rows = self._sql(
                f"SELECT toUnixTimestamp(TimeUnix) AS ts, {col} AS v, Attributes, "
                "ResourceAttributes['service.instance.id'] AS inst "
                f"FROM {self._table('otel_metrics_histogram')} "
                f"WHERE MetricName = {sql_str(base)} AND {window} ORDER BY TimeUnix"
            )
            return rows, col, True
        rows = self._sql(
            "SELECT toUnixTimestamp(TimeUnix) AS ts, Value AS v, Attributes, "
            "ResourceAttributes['service.instance.id'] AS inst "
            f"FROM {self._table('otel_metrics_sum')} "
            f"WHERE MetricName = {sql_str(name)} AND {window} ORDER BY TimeUnix"
        )
        if rows:
            return rows, "Value", True
        rows = self._sql(
            "SELECT toUnixTimestamp(TimeUnix) AS ts, Value AS v, Attributes, "
            "ResourceAttributes['service.instance.id'] AS inst "
            f"FROM {self._table('otel_metrics_gauge')} "
            f"WHERE MetricName = {sql_str(name)} AND {self._metric_window(start_s, end_s)} "
            "ORDER BY TimeUnix"
        )
        return rows, "Value", False

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
        start_ns, end_ns = int(start_s) * 1_000_000_000, int(end_s) * 1_000_000_000
        rows, _, cumulative = self._samples(name, start_s, end_s, LOOKBEHIND_S)
        samples: List[Tuple[int, float, str]] = []
        labels_of: Dict[str, str] = {}
        for row in rows:
            try:
                ts_ns = int(row.get("ts") or 0) * 1_000_000_000
                value = float(row.get("v") or 0)
            except (TypeError, ValueError):
                continue
            attrs = row.get("Attributes") if isinstance(row.get("Attributes"), dict) else {}
            ident = "|".join(f"{k}={v}" for k, v in sorted(attrs.items()))
            ident = f"{ident}|instance={row.get('inst') or ''}"
            if label:
                # The plugin's metric attributes use both spellings
                # (``gen_ai.tool.name`` and ``tool_name``); the UI asks with dots.
                value_of = attrs.get(label, attrs.get(label.replace(".", "_"), "—"))
                labels_of[ident] = str(value_of)
            else:
                labels_of[ident] = "_"
            samples.append((ts_ns, value, ident))
        if cumulative:
            increases = counter_increases(samples, window_start_ns=start_ns)
            points = [(ts, v, labels_of.get(ident, "_")) for ts, v, ident in increases]
            out = bucketize(points, start_ns, end_ns, bucket_s, agg if agg != "last" else "sum")
            out["cumulative"] = True
            out["agg"] = "sum" if agg == "last" else agg
            out["series_start_rule"] = (
                f"a series with no sample in the {LOOKBEHIND_S}s before the window "
                "counts its first value"
            )
        else:
            points = [(ts, v, labels_of.get(ident, "_")) for ts, v, ident in samples]
            out = bucketize(points, start_ns, end_ns, bucket_s, agg)
            out["agg"] = agg
        out["agg_requested"] = agg
        out["name"] = name
        return out

    # ── logs (#268 shapes) ────────────────────────────────────────────

    def _log_predicates(self, f: LogFilter, start_s: int, end_s: int) -> List[str]:
        preds = [
            f"Timestamp >= fromUnixTimestamp64Nano({int(start_s) * 1_000_000_000})",
            f"Timestamp < fromUnixTimestamp64Nano({log_end_ns(end_s, f)})",
        ]
        if f.trace_id:
            preds.append(f"TraceId = {sql_str(f.trace_id)}")
        if f.span_id:
            preds.append(f"SpanId = {sql_str(f.span_id)}")
        if f.session:
            preds.append(f"LogAttributes['hermes.session_id'] = {sql_str(f.session)}")
        if f.logger:
            preds.append(f"ScopeName = {sql_str(f.logger)}")
        if f.min_level:
            preds.append(f"SeverityNumber >= {otel_severity_for(f.min_level)}")
        if f.event_name:
            preds.append(f"LogAttributes['event.name'] = {sql_str(f.event_name)}")
        elif f.events_only:
            preds.append("LogAttributes['event.name'] != ''")
        if f.text:
            preds.append(f"Body ILIKE {sql_like(f.text)}")
        return preds

    def logs_search(
        self, f: LogFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        n = int(limit) + LOG_PAGE_SLACK
        sql = (
            "SELECT toUnixTimestamp64Nano(Timestamp) AS ts_ns, SeverityText, SeverityNumber, "
            "ScopeName, Body, TraceId, SpanId, LogAttributes "
            f"FROM {self._table(self.logs_table)} WHERE "
            + " AND ".join(self._log_predicates(f, start_s, end_s))
            + f" ORDER BY Timestamp DESC LIMIT {n}"
        )
        records: List[Dict[str, Any]] = []
        for row in self._sql(sql):
            raw = {**_coerce_map(row.get("LogAttributes")), **row}
            records.append(log_row(raw, _LOG_COLUMNS, time_ns=to_int(row.get("ts_ns"))))
        return strictly_older(records, f)

    def loggers(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        sql = (
            f"SELECT ScopeName, count() AS n FROM {self._table(self.logs_table)} "
            f"WHERE Timestamp >= fromUnixTimestamp64Nano({int(start_s) * 1_000_000_000}) "
            f"AND Timestamp < fromUnixTimestamp64Nano({int(end_s) * 1_000_000_000}) "
            "GROUP BY ScopeName ORDER BY n DESC LIMIT 200"
        )
        return [
            {"logger": str(r.get("ScopeName")), "count": to_int(r.get("n"))}
            for r in self._sql(sql)
            if r.get("ScopeName")
        ]
