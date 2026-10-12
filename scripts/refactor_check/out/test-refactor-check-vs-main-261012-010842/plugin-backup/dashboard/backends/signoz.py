"""SigNoz adapter — REST query_range API at ``/api/v4/query_range``.

Traces, metrics and logs all go through the same query-builder endpoint
(``dataSource`` selects the signal); metric names come from the
autocomplete API. Everything is normalised to the live store's shapes so
the dashboard and the ``observability`` skill render SigNoz like any
other source (#194).

Self-hosted SigNoz OSS requires authentication even on localhost. Add
an ``api_key`` (or ``api_key_env``) to the backend entry in
``config.yaml`` — generate a key in SigNoz's "Settings → API Keys".

Without a key the adapter reports ``auth_required`` in ``status()``
and raises a clear error when the UI actually tries to query.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from . import register
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
    log_end_ns,
    ns_from_any,
    ns_from_magnitude,
    otlp_attrs_from_dict,
    otlp_status,
    resolve_env_or_literal,
    rewrite_host_for_docker,
    strictly_older,
    strictly_older_traces,
    trace_page,
)

__all__ = ["SigNozAdapter", "HTTPException"]

_DEFAULT_SIGNOZ_PORT = 3301
# The trace detail asks for this many spans and says so when there are more.
_DETAIL_SPAN_CAP = 500
# The autocomplete API answers one bounded page; the catalogue is assembled
# from one search per namespace the plugin (and its host mirror) emits.
_CATALOG_NAMESPACES = ("hermes", "gen_ai", "process", "system", "hw")


def _re_esc(text: str) -> str:
    """Escape a literal for the query builder's ``regex`` operator."""
    return re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", text)


def _ms(ts_s: int) -> int:
    return int(ts_s) * 1000


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'")


# Attribute columns the query-builder asks SigNoz to return. Keeping
# this narrow keeps the response small; everything else can come back
# on the detail call.
_SIGNOZ_SELECT_COLUMNS: List[Dict[str, Any]] = [
    {"key": "traceID", "isColumn": True, "dataType": "string", "type": "tag"},
    {"key": "spanID", "isColumn": True, "dataType": "string", "type": "tag"},
    {"key": "name", "isColumn": True, "dataType": "string", "type": "tag"},
    {"key": "serviceName", "isColumn": True, "dataType": "string", "type": "tag"},
    {"key": "durationNano", "isColumn": True, "dataType": "int64", "type": "tag"},
    {"key": "timestamp", "isColumn": True, "dataType": "string", "type": "tag"},
    {"key": "parentSpanID", "isColumn": True, "dataType": "string", "type": "tag"},
    {"key": "statusCode", "isColumn": True, "dataType": "int64", "type": "tag"},
    {"key": "kind", "isColumn": True, "dataType": "int64", "type": "tag"},
]

_TAG_KEYS_TO_COLLECT = (
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

# The card attributes are span tags, not columns; a ``list`` panel returns
# only the columns it is asked for, so they are requested explicitly (#297).
# Numeric attributes live in SigNoz's number map: asked for as strings they
# come back empty, so the card's token counts need their real type (#297).
_NUMERIC_TAG_KEYS = frozenset(
    {
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.total_tokens",
        "hermes.turn.number",
    }
)
_SIGNOZ_SELECT_COLUMNS += [
    {"key": k, "type": "tag", "dataType": "float64" if k in _NUMERIC_TAG_KEYS else "string"}
    for k in _TAG_KEYS_TO_COLLECT
]


# ── metrics & logs vocabulary (#194) ─────────────────────────────────

# Metric kinds the autocomplete API reports, mapped to the query-builder's
# ``timeAggregation`` for each dashboard aggregate. Counters are shown as the
# increase per bucket (SigNoz handles delta and cumulative alike), gauges as
# the plain per-bucket reduction.
_TIME_AGGREGATION: Dict[str, Dict[str, str]] = {
    "Sum": {"sum": "increase", "count": "count", "avg": "avg", "max": "max", "last": "latest"},
    "Gauge": {"sum": "sum", "count": "count", "avg": "avg", "max": "max", "last": "latest"},
}
_SPACE_AGGREGATION: Dict[str, str] = {
    "sum": "sum",
    "count": "sum",
    "avg": "avg",
    "max": "max",
    "last": "sum",
}
# Histogram internals never make useful "instrument" rows; ``.sum``/``.count`` stay.
_METRIC_NAME_SKIP_SUFFIXES = (".bucket", ".min", ".max")

# Python logging levels → the smallest OTel severity number of that band
# (the exporter maps DEBUG→5, INFO→9, WARNING→13, ERROR→17, CRITICAL→21).
_OTEL_SEVERITY_FLOOR = {10: 5, 20: 9, 30: 13, 40: 17, 50: 21}


def _otel_severity_for(min_level: int) -> int:
    """The ``severity_number`` lower bound matching a Python ``min_level``."""
    band = max((lvl for lvl in _OTEL_SEVERITY_FLOOR if lvl <= int(min_level)), default=0)
    return _OTEL_SEVERITY_FLOOR.get(band, 1)


def _column(key: str, data_type: str = "string") -> Dict[str, Any]:
    """A filter/group key that is a real column of the logs table."""
    return {"key": key, "type": "", "dataType": data_type, "isColumn": True}


# The resource attribute that tells one exporting process from another.
_INSTANCE_KEY = "service.instance.id"


def _tag(key: str, data_type: str = "string") -> Dict[str, Any]:
    """A filter/group key that is a record attribute."""
    return {"key": key, "type": "tag", "dataType": data_type}


def _builder_query(**overrides: Any) -> Dict[str, Any]:
    """The query-builder skeleton every metrics/logs request shares."""
    q: Dict[str, Any] = {
        "queryName": "A",
        "expression": "A",
        "disabled": False,
        "stepInterval": 60,
        "filters": {"op": "AND", "items": []},
    }
    q.update(overrides)
    return q


def _composite(
    query: Dict[str, Any], panel: str, start_s: int, end_s: int, step_s: int
) -> Dict[str, Any]:
    return {
        "start": _ms(start_s),
        "end": _ms(end_s),
        "step": int(step_s),
        "compositeQuery": {
            "queryType": "builder",
            "panelType": panel,
            "builderQueries": {"A": query},
        },
    }


@register
class SigNozAdapter(BackendAdapter):
    handles = frozenset({"signoz"})
    query_lang_label = "SigNoz filter (key=value)"
    raw_placeholder = "serviceName=hermes-agent"
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
        port = cfg.get("query_port") or _DEFAULT_SIGNOZ_PORT
        self.query_url = f"{scheme}://{rewrite_host_for_docker(host)}:{port}"
        self.api_key = resolve_env_or_literal(cfg, "api_key", "api_key_env")
        # The metrics catalog, once per instance (instances are cached while
        # the config is unchanged, #290): one request per chart, not two.
        self._catalog_cache: Optional[List[Dict[str, Any]]] = None

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        base["auth_required"] = self.api_key is None
        if self.api_key is None:
            base["auth_hint"] = (
                "SigNoz OSS requires authentication. Add api_key or "
                "api_key_env to the signoz backend entry — generate a "
                "key under 'Settings → API Keys' in the SigNoz UI."
            )
        return base

    # ── HTTP helpers ─────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise ConfigError(
                "SigNoz requires an api_key. Add api_key or api_key_env "
                "to the signoz backend entry in config.yaml."
            )
        # A personal access token is honoured as ``SIGNOZ-API-KEY``; a session
        # JWT (what ``/api/v2/sessions/email_password`` returns) only as a
        # Bearer. Send both so either kind of key works (verified on v0.119).
        return {"SIGNOZ-API-KEY": self.api_key, "Authorization": f"Bearer {self.api_key}"}

    # ── Query-builder JSON construction ──────────────────────────────

    def _parse_raw_filters(self, raw: Optional[str]) -> List[Dict[str, Any]]:
        """Parse ``key=value key2~=regex`` pairs from the raw input.

        Supported ops (user typed between key and value):
            ``=``    exact match on tag
            ``!=``   not equal
            ``~=``   regex
            ``>=``   numeric ≥
        The raw grammar is intentionally terse: it is the query builder's
        own filter items, one per token.
        """
        if not raw:
            return []
        items: List[Dict[str, Any]] = []
        for token in raw.split():
            for op_sym, op_name in (("!=", "!="), ("~=", "regex"), (">=", ">="), ("=", "=")):
                if op_sym in token:
                    k, v = token.split(op_sym, 1)
                    if k and v:
                        items.append(
                            {
                                "key": {"key": k.strip(), "type": "tag"},
                                "op": op_name,
                                "value": v.strip().strip("\"'"),
                            }
                        )
                    break
        return items

    def _build_filters(self, f: StructuredFilter) -> Dict[str, Any]:
        items: List[Dict[str, Any]] = []
        if f.service:
            items.append(
                {
                    "key": {"key": "serviceName", "type": "tag"},
                    "op": "=",
                    "value": f.service,
                }
            )
        if f.name_prefix:
            items.append(
                {
                    "key": {"key": "name", "type": "tag"},
                    "op": "regex",
                    "value": "^" + _re_esc(f.name_prefix),
                }
            )
        if f.name_regex:
            items.append(
                {
                    "key": {"key": "name", "type": "tag"},
                    "op": "regex",
                    "value": f.name_regex,
                }
            )
        if f.status in ("error", "ok"):
            items.append(
                {
                    "key": {"key": "hasError", "type": "tag"},
                    "op": "=",
                    "value": f.status == "error",
                }
            )
        for k, v in f.attr_equals.items():
            items.append({"key": {"key": k, "type": "tag"}, "op": "=", "value": str(v)})
        if f.free_text:
            # The captured prompt of an api span; SigNoz's builder has no OR
            # across keys, so this searches the one attribute every turn has.
            items.append(
                {
                    "key": {"key": "input.value", "type": "tag"},
                    "op": "contains",
                    "value": f.free_text,
                }
            )
        if f.roots_only:
            items.append(
                {
                    "key": {"key": "parentSpanID", "type": "tag"},
                    "op": "=",
                    "value": "",
                }
            )
        items.extend(self._parse_raw_filters(f.raw))
        return {"op": "AND", "items": items}

    def _build_query_body(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> Dict[str, Any]:
        end_ms = _ms(end_s)
        if f.before_ns:
            # The cursor is the end bound (ms, rounded up so the row it was
            # taken from stays inside); strictly_older_traces() cuts exactly.
            end_ms = min(end_ms, int(f.before_ns) // 1_000_000 + 1)
        # ``limit`` counts span rows; without roots-only several rows of one
        # trace share a page, so ask for more and dedupe (#297). One beyond
        # the page so has_more is exact.
        rows_wanted = int(limit) + 1 if f.roots_only else int(limit) * 4 + 1
        body = {
            "start": _ms(start_s),
            "end": end_ms,
            "step": 60,
            "compositeQuery": {
                "queryType": "builder",
                "panelType": "list",
                "builderQueries": {
                    "A": {
                        "queryName": "A",
                        "dataSource": "traces",
                        "aggregateOperator": "noop",
                        "aggregateAttribute": {"key": "", "type": "tag"},
                        "expression": "A",
                        "disabled": False,
                        "stepInterval": 60,
                        "filters": self._build_filters(f),
                        "orderBy": [{"columnName": "timestamp", "order": "desc"}],
                        "limit": rows_wanted,
                        "selectColumns": _SIGNOZ_SELECT_COLUMNS,
                    }
                },
            },
        }
        if f.min_duration_ms and f.min_duration_ms > 0:
            body["compositeQuery"]["builderQueries"]["A"]["filters"]["items"].append(
                {
                    "key": {"key": "durationNano", "type": "tag"},
                    "op": ">=",
                    "value": int(f.min_duration_ms) * 1_000_000,
                }
            )
        return body

    # ── Public API ───────────────────────────────────────────────────

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        body = self._build_query_body(f, start_s, end_s, limit)
        url = f"{self.query_url}/api/v4/query_range"
        data = http_post_json(url, body, headers=self._headers(), timeout=15.0)

        # v4 response shape: {data: {result: [{list: [{data: {...span columns}}]}]}}.
        rows = _extract_v4_list_rows(data)

        traces: List[Dict[str, Any]] = []
        seen: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            trace_id = row.get("traceID") or row.get("traceId")
            if not trace_id:
                continue
            if trace_id in seen:
                continue
            seen[trace_id] = row
            start_ns = _ns_from_row(row)
            duration_ns = int(row.get("durationNano") or 0)
            attrs_map: Dict[str, Any] = {
                "name": row.get("name"),
                "status": "error" if row.get("hasError") or row.get("statusCode") == 2 else "ok",
            }
            for k in _TAG_KEYS_TO_COLLECT:
                v = row.get(k)
                if v is not None:
                    attrs_map[k] = v
            traces.append(
                {
                    "traceID": trace_id,
                    "rootServiceName": row.get("serviceName") or "",
                    "rootTraceName": row.get("name") or "",
                    "startTimeUnixNano": str(start_ns) if start_ns else "0",
                    "durationMs": duration_ns // 1_000_000 if duration_ns else 0,
                    "spanSets": [
                        {
                            "spans": [
                                {
                                    "spanID": row.get("spanID") or row.get("spanId"),
                                    "name": row.get("name") or "",
                                    "attributes": otlp_attrs_from_dict(attrs_map),
                                }
                            ],
                            "matched": 1,
                        }
                    ],
                }
            )
        return trace_page(strictly_older_traces(traces, f), limit)

    def trace_url(self, trace_id: str) -> Optional[str]:
        """The trace in SigNoz's UI: ``ui_url`` on the entry when set, else the
        query origin (the UI and the API share the port)."""
        base = str(self.cfg.get("ui_url") or self.query_url or "").rstrip("/")
        return f"{base}/trace/{trace_id}" if base else None

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        # ``GET /api/v1/traces/{traceID}`` is the span list (columns/events)
        # on every build seen, v0.119 included; the POST with
        # ``spansRenderLimit`` that some builds accept answers the SPA page on
        # v0.119 (verified live, #297), so it is only a fallback for a GET
        # refused as a request (4xx), never after a network failure.
        headers = self._headers()
        url = f"{self.query_url}/api/v1/traces/{trace_id}"
        try:
            data = http_get_json(url, headers=headers, timeout=20.0)
        except BackendError as exc:
            if exc.kind == "not_found":
                raise BackendError(404, f"Trace {trace_id} not found in SigNoz", "not_found")
            if exc.kind != "backend" or "Backend returned 4" not in str(exc.detail):
                raise
            data = http_post_json(
                url,
                {"spansRenderLimit": _DETAIL_SPAN_CAP, "uncollapsedSpans": []},
                headers=headers,
                timeout=20.0,
            )
        out = _signoz_trace_to_otlp(data)
        n = sum(
            len(scope.get("spans") or [])
            for batch in out.get("batches") or []
            for scope in batch.get("scopeSpans") or []
        )
        if n == 0:
            raise BackendError(404, f"Trace {trace_id} not found in SigNoz", "not_found")
        out["span_count"] = n
        out["truncated"] = n >= _DETAIL_SPAN_CAP
        return out

    # ── metrics (#194) ────────────────────────────────────────────────

    def _query_range(self, body: Dict[str, Any], timeout: float = 20.0) -> Any:
        return http_post_json(
            f"{self.query_url}/api/v4/query_range", body, headers=self._headers(), timeout=timeout
        )

    def _metric_catalog(self, search: str = "") -> List[Dict[str, Any]]:
        """``[{key, type, dataType}]`` from the metrics autocomplete API.

        SigNoz has no "instruments with data in this window" call; the
        catalog is everything the store has ever seen, which for a plugin
        namespace like ``hermes.*`` is exactly the instrument list.
        """
        if not search and self._catalog_cache is not None:
            return self._catalog_cache

        def fetch(text: str) -> List[Dict[str, Any]]:
            qs = _urlparse.urlencode(
                {"aggregateOperator": "sum", "dataSource": "metrics", "searchText": text}
            )
            data = http_get_json(
                f"{self.query_url}/api/v3/autocomplete/aggregate_attributes?{qs}",
                headers=self._headers(),
                timeout=15.0,
            )
            keys = (
                ((data.get("data") or {}).get("attributeKeys")) if isinstance(data, dict) else None
            )
            return [k for k in keys or [] if isinstance(k, dict) and k.get("key")]

        if search:
            return fetch(search)
        # The autocomplete answers a bounded page (``hermes.token.usage`` fell
        # off a 41-entry list on v0.119); one search per namespace the plugin
        # emits keeps every instrument.
        out: List[Dict[str, Any]] = []
        seen: set = set()
        for ns in _CATALOG_NAMESPACES:
            for k in fetch(ns):
                if k["key"] not in seen:
                    seen.add(k["key"])
                    out.append(k)
        self._catalog_cache = out
        return out

    def metric_names(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        names = sorted(
            {
                k["key"]
                for k in self._metric_catalog()
                if not str(k["key"]).endswith(_METRIC_NAME_SKIP_SUFFIXES)
            }
        )
        return [{"name": n} for n in names]

    def _metric_kind(self, name: str) -> str:
        """``Sum`` / ``Gauge`` / ``Histogram`` for one instrument (``Sum`` if unknown).

        Answered from the cached catalog when it has the name, so a chart
        refresh is one request."""
        for k in self._metric_catalog():
            if k.get("key") == name:
                return str(k.get("type") or "Sum")
        for k in self._metric_catalog(name):
            if k.get("key") == name:
                return str(k.get("type") or "Sum")
        return "Sum"

    def metrics_query(
        self,
        name: str,
        start_s: int,
        end_s: int,
        bucket_s: int,
        group_by: Optional[str] = None,
        agg: str = "sum",
    ) -> Dict[str, Any]:
        kind = self._metric_kind(name)
        cumulative = kind == "Sum" and agg in ("sum", "count", "last")
        if cumulative:
            # A counter's value is per exporting process. SigNoz's own
            # ``increase`` over-counted one-shot turns that share a step
            # (verified on v0.119: four turns of 2+1+2+4 calls read 10), so
            # the adapter reads the latest cumulative value per process and
            # step (``service.instance.id`` joins the grouping as a series key,
            # never a label) and takes the increases itself, a series that
            # starts inside the window counting its first value (#297).
            time_agg = "latest"
            group = ([_tag(group_by)] if group_by else []) + [_tag(_INSTANCE_KEY)]
        else:
            time_agg = _TIME_AGGREGATION.get(kind, _TIME_AGGREGATION["Sum"]).get(agg, "sum")
            group = [_tag(group_by)] if group_by else []
        query = _builder_query(
            dataSource="metrics",
            aggregateAttribute={"key": name, "dataType": "float64", "type": kind, "isColumn": True},
            timeAggregation=time_agg,
            spaceAggregation=_SPACE_AGGREGATION.get(agg, "sum"),
            groupBy=group,
            stepInterval=int(bucket_s),
        )
        # SigNoz stamps each step's value at the step's START on a grid aligned
        # to multiples of ``stepInterval`` since the epoch, so a sample taken 11 s
        # into a minute comes back at the minute boundary, *before* a window
        # that opened at :11. Ask from that boundary and treat it as the
        # window's start, otherwise the first-value rule below drops every
        # series that began inside a short window (verified on v0.119:
        # a 46 s batch read 0 tokens / 0 calls, #297).
        step = max(1, int(bucket_s))
        grid_start_s = int(start_s) - int(start_s) % step
        data = self._query_range(_composite(query, "graph", grid_start_s, end_s, bucket_s))
        if cumulative:
            samples = [
                (
                    ts_ns,
                    value,
                    f"{labels.get(group_by, '—') if group_by else '_'}\x00{labels.get(_INSTANCE_KEY, '')}",
                )
                for labels, ts_ns, value in _iter_series_points(data)
            ]
            points = [
                (ts_ns, value, ident.split("\x00", 1)[0])
                for ts_ns, value, ident in counter_increases(
                    samples, window_start_ns=grid_start_s * 1_000_000_000
                )
            ]
        else:
            points = [
                (ts_ns, value, labels.get(group_by, "_") if group_by else "_")
                for labels, ts_ns, value in _iter_series_points(data)
            ]
        # SigNoz already reduced each bucket, so folding one point per bucket
        # with ``sum`` reproduces its values on the shared bucket grid.
        out = bucketize(points, start_s * 1_000_000_000, end_s * 1_000_000_000, bucket_s, "sum")
        out["agg"] = agg
        out["name"] = name
        out["kind"] = kind
        out["cumulative"] = cumulative
        # A ``Sum`` instrument is a counter shown as the increase per bucket.
        out["cumulative"] = kind == "Sum"
        return out

    # ── logs (#194) ───────────────────────────────────────────────────

    def _log_filter_items(self, f: LogFilter) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if f.trace_id:
            items.append({"key": _column("trace_id"), "op": "=", "value": f.trace_id})
        if f.span_id:
            items.append({"key": _column("span_id"), "op": "=", "value": f.span_id})
        if f.session:
            items.append({"key": _tag("hermes.session_id"), "op": "=", "value": f.session})
        if f.logger:
            # The OTLP logs exporter records the Python logger name as the
            # instrumentation scope, which SigNoz keeps in ``scope_name``.
            items.append({"key": _column("scope_name"), "op": "=", "value": f.logger})
        if f.min_level:
            items.append(
                {
                    "key": _column("severity_number", "int64"),
                    "op": ">=",
                    "value": _otel_severity_for(f.min_level),
                }
            )
        if f.text:
            items.append({"key": _column("body"), "op": "contains", "value": f.text})
        # Structured events carry their name as the ``event.name`` attribute
        # (SigNoz keeps no column for the OTLP ``event_name`` field); verified
        # against SigNoz v0.119 (#268).
        if f.event_name:
            items.append({"key": _tag("event.name"), "op": "=", "value": f.event_name})
        elif f.events_only:
            items.append({"key": _tag("event.name"), "op": "exists"})
        if f.before_ns:
            # Keyset paging at nanosecond precision (``timestamp`` is a ns column).
            items.append(
                {"key": _column("timestamp", "int64"), "op": "<", "value": int(f.before_ns)}
            )
        return items

    def logs_search(
        self, f: LogFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        query = _builder_query(
            dataSource="logs",
            aggregateOperator="noop",
            aggregateAttribute={"key": "", "type": "tag"},
            filters={"op": "AND", "items": self._log_filter_items(f)},
            orderBy=[{"columnName": "timestamp", "order": "desc"}],
            limit=int(limit),
        )
        body = _composite(query, "list", start_s, end_s, 60)
        # The window end is in ms; the filter item above cuts at the exact
        # nanosecond, so end at the ms that holds the cursor.
        body["end"] = log_end_ns(end_s, f) // 1_000_000 + (1 if f.before_ns else 0)
        data = self._query_range(body)
        return strictly_older([_log_record(e) for e in _extract_v4_list_entries(data)], f)

    def loggers(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        query = _builder_query(
            dataSource="logs",
            aggregateOperator="count",
            aggregateAttribute={"key": "", "type": "tag"},
            groupBy=[_column("scope_name")],
            reduceTo="sum",
            limit=200,
        )
        data = self._query_range(_composite(query, "table", start_s, end_s, 60))
        out = []
        for labels, _ts, value in _iter_series_points(data):
            logger = labels.get("scope_name")
            if logger:
                out.append({"logger": logger, "count": int(value)})
        return sorted(out, key=lambda r: -r["count"])


# ── Helpers: response shape normalization ─────────────────────────────


def _extract_v4_list_entries(data: Any) -> List[Dict[str, Any]]:
    """The ``{timestamp, data: {...}}`` entries of a ``panelType: list`` response."""
    if not isinstance(data, dict):
        return []
    entries: List[Dict[str, Any]] = []
    for series in ((data.get("data") or {}).get("result")) or []:
        for entry in series.get("list") or []:
            if isinstance(entry, dict) and isinstance(entry.get("data"), dict):
                entries.append(entry)
    return entries


def _iter_series_points(data: Any):
    """Yield ``(labels, ts_ns, value)`` from a ``graph``/``table`` response.

    Shape: ``{data: {result: [{series: [{labels: {...}, values: [{timestamp:
    ms, value: "str"}]}]}]}}``; ``timestamp`` is 0 for table reductions.
    """
    if not isinstance(data, dict):
        return
    for result in ((data.get("data") or {}).get("result")) or []:
        for series in result.get("series") or []:
            labels = series.get("labels") or {}
            for point in series.get("values") or []:
                try:
                    value = float(point.get("value"))
                except (TypeError, ValueError):
                    continue
                yield labels, int(point.get("timestamp") or 0) * 1_000_000, value


def _log_record(entry: Dict[str, Any]) -> Dict[str, Any]:
    """One SigNoz log entry → the live store's record shape (#268: with span id,
    severity number, event name and every attribute)."""
    row = entry.get("data") or {}
    attrs: Dict[str, Any] = {}
    for bucket in (
        "attributes_string",
        "attributes_number",
        "attributes_bool",
        "attributes_float64",
        "attributes_int64",
    ):
        values = row.get(bucket)
        if isinstance(values, dict):
            attrs.update(values)
    resources = row.get("resources_string") or {}
    # ``finish_log_row`` lifts ``event_name`` / ``event.name`` out of the attributes.
    base = {
        "level": str(row.get("severity_text") or "INFO").upper(),
        "severity_number": row.get("severity_number"),
        "logger": row.get("scope_name") or "",
        "body": row.get("body") or "",
        "time_unix_nano": ns_from_any(entry.get("timestamp")) or 0,
        "trace_id": row.get("trace_id") or None,
        "span_id": row.get("span_id") or None,
        "session_id": attrs.get("hermes.session_id") or None,
        "event_name": row.get("event_name") or None,
        "attributes": attrs,
    }
    if isinstance(resources, dict) and resources:
        base["attributes"] = {**attrs, **{f"resource.{k}": v for k, v in resources.items()}}
    return finish_log_row(base)


def _extract_v4_list_rows(data: Any) -> List[Dict[str, Any]]:
    """Flatten v4 ``panelType:list`` response into a flat list of span rows."""
    if not isinstance(data, dict):
        return []
    result = ((data.get("data") or {}).get("result")) or []
    rows: List[Dict[str, Any]] = []
    for series in result:
        for entry in series.get("list") or []:
            row = entry.get("data") if isinstance(entry, dict) else None
            if isinstance(row, dict):
                # the span's start time is on the entry, not in its columns
                if not row.get("timestamp") and entry.get("timestamp"):
                    row = {**row, "timestamp": entry["timestamp"]}
                rows.append(row)
    return rows


def _ns_from_row(row: Dict[str, Any]) -> Optional[int]:
    ts = row.get("timestamp")
    if isinstance(ts, str) and ts:
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1_000_000_000)
        except Exception:
            return None
    if isinstance(ts, (int, float)):
        # SigNoz sometimes returns ns directly; sometimes ms. Scale by magnitude.
        return ns_from_magnitude(ts)
    return None


_REF_SPAN = re.compile(r"SpanId=([0-9a-fA-F]*)")


def _signoz_rows_to_spans(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rows of the columns/events trace shape as the older ``spans`` dicts.

    Columns seen: ``__time`` (ms), ``SpanId``, ``TraceId``, ``ServiceName``,
    ``Name``, ``Kind``, ``DurationNano``, ``TagsKeys`` / ``TagsValues``,
    ``References`` (``"{TraceId=…, SpanId=<parent>, RefType=CHILD_OF}"``
    strings; an empty SpanId marks the root), ``HasError``,
    ``StatusMessage``, ``StatusCodeString``, ``SpanKind``.
    """
    cols = [str(c) for c in entry.get("columns") or []]
    out: List[Dict[str, Any]] = []
    for row in entry.get("events") or []:
        if not isinstance(row, list):
            continue
        r = dict(zip(cols, row))
        parent = None
        for ref in r.get("References") or []:
            if "CHILD_OF" in str(ref):
                m = _REF_SPAN.search(str(ref))
                parent = (m.group(1) or None) if m else None
                break
        keys, values = r.get("TagsKeys") or [], r.get("TagsValues") or []
        tags = (
            dict(zip(keys, values)) if isinstance(keys, list) and isinstance(values, list) else {}
        )
        out.append(
            {
                "spanID": r.get("SpanId"),
                "traceID": r.get("TraceId"),
                "parentSpanID": parent,
                "name": r.get("Name"),
                "serviceName": r.get("ServiceName"),
                "timestamp": r.get("__time"),
                "durationNano": r.get("DurationNano"),
                "kind": r.get("Kind"),
                "hasError": bool(r.get("HasError")),
                "statusMessage": r.get("StatusMessage") or "",
                "tagMap": tags,
            }
        )
    return out


def _signoz_trace_to_otlp(data: Any) -> Dict[str, Any]:
    """Translate SigNoz's trace response shape into OTLP batches.

    SigNoz's shape varies by version; we handle the two known forms:

    * ``{spans: [{spanID, parentSpanID, name, serviceName,
      timestamp, durationNano, tagMap/tagsMap: {...}}]}``
    * ``{data: {spans: [...]}}``
    """
    if isinstance(data, list):
        # Current SigNoz (v0.90+): ``[{columns: [...], events: [[...], ...]}]``,
        # one row per span with the columns named in ``columns``.
        data = {
            "spans": [
                sp
                for entry in data
                if isinstance(entry, dict) and entry.get("columns")
                for sp in _signoz_rows_to_spans(entry)
            ]
        }
    if not isinstance(data, dict):
        return {"batches": []}
    spans_raw = data.get("spans")
    if spans_raw is None:
        spans_raw = (data.get("data") or {}).get("spans")
    if not isinstance(spans_raw, list):
        return {"batches": []}

    by_service: Dict[str, List[Dict[str, Any]]] = {}
    for sp in spans_raw:
        if not isinstance(sp, dict):
            continue
        service = sp.get("serviceName") or ""
        attrs: Dict[str, Any] = {}
        tagmap = sp.get("tagMap") or sp.get("tagsMap") or sp.get("tags") or {}
        if isinstance(tagmap, dict):
            for k, v in tagmap.items():
                attrs[k] = v
        elif isinstance(tagmap, list):
            for t in tagmap:
                if isinstance(t, dict) and "key" in t:
                    attrs[t["key"]] = t.get("value")

        start_ns = _ns_from_row(sp)
        duration_ns = int(sp.get("durationNano") or 0)
        end_ns = (start_ns + duration_ns) if start_ns else 0

        otlp_span = {
            "traceId": sp.get("traceID") or sp.get("traceId"),
            "spanId": sp.get("spanID") or sp.get("spanId"),
            "parentSpanId": sp.get("parentSpanID") or sp.get("parentSpanId") or None,
            "name": sp.get("name") or "",
            "kind": int(sp.get("kind") or 1),
            "startTimeUnixNano": str(start_ns) if start_ns else "0",
            "endTimeUnixNano": str(end_ns) if end_ns else "0",
            "attributes": otlp_attrs_from_dict(attrs),
            "status": otlp_status(
                "error" if sp.get("hasError") else "ok", str(sp.get("statusMessage") or "")
            ),
        }
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
