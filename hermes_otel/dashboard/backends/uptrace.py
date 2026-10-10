"""Uptrace adapter — the ``/internal/v1`` query API of Uptrace 2.x.

Uptrace 2 serves traces, logs and metrics from one API, authenticated with a
**user** token (``Settings → API tokens`` in the UI, or the seeded
``user_tokens`` entry of a self-hosted config). The DSN's project token only
ingests; the adapter still reads the DSN for the host when no ``endpoint``
is set. Uptrace defaults to project 1 on a fresh install; set ``project_id``
otherwise.

Two dialects of that API exist, and the adapter probes once per process
(``/internal/v1/logs/{project}/systems``) to learn which one it is talking to:

* **2.1** (verified against 2.1.0-beta.5 with its own UI's requests, #268):
  one route per signal — ``/spans/{p}``, ``/logs/{p}``, ``/traces/{p}/{id}``,
  ``/metrics/{p}`` — with ``time_start`` / ``time_end`` (ms), repeated
  ``system[]`` values, ``sort_by=_time&sort_dir=desc``, and attribute keys
  carrying a type suffix (``hermes_session_id::str``) that the adapter strips.
* **2.0** (shapes recorded from 2.0.2 in #243): everything under
  ``/tracing/{p}/…`` with ``time_gte`` / ``time_lt``, repeated ``system``
  values, ``sort_desc=true`` and bare attribute keys.

In both, UQL ``where`` clauses filter rows and ``search=`` is a full-text
match; log rows are the span store's ``log:<level>`` systems, the Python
logger is ``otel_library_name``, the body is ``displayName`` and a structured
event's name is the ``event_name`` attribute (Uptrace's own ``eventName``
field is its row kind, ``log`` or ``exception``). Attribute keys are
flattened with underscores (``gen_ai_request_model``), which :func:`_dotted`
maps back to the documented dotted names for the UI.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib import parse as _urlparse

from . import default_service_name, register
from ._attrs import dotted as _dotted
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
    log_end_ns,
    otlp_attrs_from_dict,
    otlp_status,
    resolve_env_or_literal,
    rewrite_host_for_docker,
    strictly_older,
    strictly_older_traces,
    trace_page,
)

__all__ = ["UptraceAdapter", "HTTPException"]

# Entries that switch the service pin on log queries off.
_NO_PIN = ("", "off", "none", "any", "*")

_DEFAULT_UPTRACE_HTTP_PORT = 14318
_API = "/internal/v1"

# How each Uptrace 2.x release spells the same query API (see the module
# docstring). ``{p}`` is the project id, ``{trace_id}`` the trace.
_DIALECTS: Dict[str, Dict[str, Any]] = {
    "2.1": {
        "start": "time_start",
        "end": "time_end",
        "system_key": "system[]",
        "sort": (("sort_by", "_time"), ("sort_dir", "desc")),
        "spans": "/spans/{p}",
        "span_systems": ("spans:all",),
        # 2.1 filters on the parent id column, so roots come from the server
        "root_filter": 'where _parent_id = ""',
        "logs": "/logs/{p}",
        "trace": "/traces/{p}/{trace_id}",
        "logger_values": "/tracing/{p}/attributes/otel_library_name::str",
        "logger_params": (("space", "logs"),),
    },
    "2.0": {
        "start": "time_gte",
        "end": "time_lt",
        "system_key": "system",
        "sort": (("sort_by", "_time"), ("sort_desc", "true")),
        "spans": "/tracing/{p}/spans",
        "span_systems": (),
        "logs": "/tracing/{p}/spans",
        "trace": "/tracing/{p}/traces/{trace_id}/spans",
        "logger_values": "/tracing/{p}/attributes/otel_library_name",
        "logger_params": (),
    },
}
_DIALECT_CACHE: Dict[str, str] = {}

# Card attributes, in the underscored form Uptrace stores them.
_CARD_ATTRS = (
    "llm_model_name",
    "llm_provider",
    "llm_api_mode",
    "gen_ai_usage_input_tokens",
    "gen_ai_usage_output_tokens",
    "gen_ai_usage_total_tokens",
    "llm_response_finish_reason",
    "llm_response_tool_calls",
    "tool_name",
    "input_value",
    "output_value",
    "llm_output_content",
)

# MQL per Uptrace instrument kind and dashboard aggregate. A counter only
# supports its per-interval sum (``$m``); the other verbs fall back to it and
# the response says which aggregate was actually applied.
_MQL: Dict[str, Dict[str, str]] = {
    "counter": {"sum": "$m", "count": "$m", "avg": "$m", "max": "$m", "last": "$m"},
    "additive": {
        "sum": "sum($m)",
        "count": "count($m)",
        "avg": "avg($m)",
        "max": "max($m)",
        "last": "$m",
    },
    "gauge": {
        "sum": "sum($m)",
        "count": "count($m)",
        "avg": "avg($m)",
        "max": "max($m)",
        "last": "$m",
    },
    "histogram": {
        "sum": "sum($m)",
        "count": "count($m)",
        "avg": "avg($m)",
        "max": "max($m)",
        "last": "avg($m)",
    },
}

# Uptrace log "systems" from least to most severe; a Python ``min_level``
# selects the tail of this list.
_LOG_SYSTEMS = (
    "log:trace",
    "log:debug",
    "log:info",
    "log:warn",
    "log:error",
    "log:fatal",
    "log:panic",
)
_LEVEL_TO_SYSTEM_INDEX = {10: 1, 20: 2, 30: 3, 40: 4, 50: 5}


def _extract_dsn(dsn: Optional[str]) -> Optional[str]:
    """The HTTP base (``scheme://host:port``) of an Uptrace DSN, if any."""
    if not dsn:
        return None
    try:
        parsed = _urlparse.urlparse(dsn)
    except Exception:
        return None
    if not parsed.hostname:
        return None
    return (
        f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port or _DEFAULT_UPTRACE_HTTP_PORT}"
    )


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _ms(ts_s: int) -> int:
    return int(ts_s) * 1000


def _ns_from_ms(ms: Any) -> int:
    """Uptrace's millisecond floats carry microsecond precision; round there,
    not at the nanosecond, or float error shows up in the last digits."""
    try:
        return int(round(float(ms) * 1000)) * 1000
    except (TypeError, ValueError):
        return 0


def plain_attrs(raw: Any) -> Dict[str, Any]:
    """Uptrace 2.1 suffixes every attribute key with its type
    (``hermes_session_id::str``, ``code_line_number::int``); 2.0 does not.
    Either way, the adapter works with the bare underscored key."""
    if not isinstance(raw, dict):
        return {}
    return {str(k).split("::", 1)[0]: v for k, v in raw.items()}


def log_systems_for(min_level: int) -> List[str]:
    """The ``system=`` values covering Python levels ``>= min_level``."""
    if not min_level:
        return ["log:all"]
    band = max((lvl for lvl in _LEVEL_TO_SYSTEM_INDEX if lvl <= int(min_level)), default=10)
    return list(_LOG_SYSTEMS[_LEVEL_TO_SYSTEM_INDEX[band] :])


# The resource attribute that tells one exporting process from another.
_INSTANCE_KEY = "service_instance_id"


def mql_for(instrument: str, agg: str, group_by: Optional[str]) -> str:
    expr = _MQL.get(instrument, _MQL["gauge"]).get(agg, "$m")
    return f"{expr} group by {group_by.replace('.', '_')}" if group_by else expr


@register
class UptraceAdapter(BackendAdapter):
    handles = frozenset({"uptrace"})
    query_lang_label = "UQL"
    raw_placeholder = 'where _name like "api.%" | where _duration > 1s'
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
        "roots_only": "client",  # kept from each row's parentId (2.0 has no parent column)
    }

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        base = _extract_dsn(resolve_env_or_literal(cfg, "dsn", "dsn_env"))
        endpoint = cfg.get("endpoint") or ""
        if endpoint:
            parsed = _urlparse.urlparse(endpoint)
            base = (
                f"{parsed.scheme or 'http'}://{parsed.hostname or 'localhost'}:"
                f"{cfg.get('query_port') or _DEFAULT_UPTRACE_HTTP_PORT}"
            )
        if base:
            parsed = _urlparse.urlparse(base)
            base = f"{parsed.scheme}://{rewrite_host_for_docker(parsed.hostname or 'localhost')}:{parsed.port}"
        self.query_url = base or ""
        self.token = (
            resolve_env_or_literal(cfg, "user_token", "user_token_env")
            or os.environ.get("UPTRACE_USER_TOKEN", "").strip()
            or None
        )
        self.project_id = int(cfg.get("project_id") or 1)
        # Trace and log queries are pinned to the agent's service (Uptrace
        # writes its own spans and lines into the same project): the entry's ``service_name``, else the
        # plugin's resource ``service.name``, else ``hermes-agent``;
        # ``service_name: off`` removes the pin.
        raw_service = cfg.get("service_name")
        if isinstance(raw_service, str) and raw_service.strip().lower() in _NO_PIN:
            self.service_name: Optional[str] = None
        elif raw_service is False:
            self.service_name = None
        else:
            self.service_name = default_service_name(cfg)
        self._instrument_cache: Dict[str, str] = {}

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        base["project_id"] = self.project_id
        base["service_name"] = self.service_name
        base["auth_required"] = self.token is None
        if self.query_url in _DIALECT_CACHE:
            base["api_dialect"] = _DIALECT_CACHE[self.query_url]
        if self.token is None:
            base["auth_hint"] = (
                "Uptrace's query API needs a user token (Settings → API tokens, or the "
                "seeded user_tokens entry), not the DSN's project token. Set user_token "
                "or user_token_env on the uptrace backend entry."
            )
        return base

    def trace_url(self, trace_id: str) -> Optional[str]:
        return f"{self.query_url}/traces/{trace_id}" if self.query_url else None

    # ── HTTP ──────────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        if not self.query_url:
            raise ConfigError(
                "The uptrace backend entry names no server: set endpoint (or dsn / dsn_env)."
            )
        if not self.token:
            raise ConfigError("Uptrace requires a user token. Set user_token or user_token_env.")
        return {"Authorization": f"Bearer {self.token}"}

    def _dialect(self) -> Dict[str, Any]:
        """Which spelling of the API this server speaks; probed once per process.

        ``/logs/{p}/systems`` exists only in 2.1 — 2.0 answers it with the SPA's
        HTML (a "non-JSON" 502 here). A JSON error (validation, a slow
        ClickHouse) still means the route exists; an unreachable server or a
        rejected token is reported as such rather than guessed, and nothing
        is cached for it (#298).
        """
        version = _DIALECT_CACHE.get(self.query_url)
        if version is None:
            now = int(time.time())
            query = [("time_start", _ms(now - 60)), ("time_end", _ms(now))]
            url = f"{self.query_url}{_API}/logs/{self.project_id}/systems?{_urlparse.urlencode(query)}"
            version = "2.1"
            try:
                http_get_json(url, headers=self._headers(), timeout=20.0)
            except HTTPException as exc:
                detail = str(exc.detail)
                kind = getattr(exc, "kind", "backend")
                if kind == "auth":
                    raise
                if "non-JSON" in detail:
                    version = "2.0"
                elif "Backend returned" not in detail:
                    raise
            _DIALECT_CACHE[self.query_url] = version
        return _DIALECTS[version]

    def _forget_dialect(self) -> None:
        """Drop the probed dialect: after an auth error the next request with
        a corrected token probes again instead of trusting a stale answer."""
        _DIALECT_CACHE.pop(self.query_url, None)

    def _path(self, key: str, **fmt: Any) -> str:
        return str(self._dialect()[key]).format(p=self.project_id, **fmt)

    def _api_url(self, path: str, query: Iterable[Tuple[str, Any]]) -> str:
        encoded = _urlparse.urlencode(list(query))
        return f"{self.query_url}{_API}{path}" + (f"?{encoded}" if encoded else "")

    def _get(
        self,
        path: str,
        start_s: int,
        end_s: int,
        params: Iterable[Tuple[str, Any]] = (),
        end_ms: Optional[int] = None,
    ) -> Any:
        """GET ``/internal/v1<path>`` with the time window in the server's dialect."""
        d = self._dialect()
        query = [(d["start"], _ms(start_s)), (d["end"], end_ms or _ms(end_s)), *params]
        try:
            data = http_get_json(self._api_url(path, query), headers=self._headers(), timeout=20.0)
        except BackendError as exc:
            if exc.kind in ("auth", "not_found"):
                self._forget_dialect()
            raise
        _raise_query_errors(data)
        return data

    def _systems(self, systems: Iterable[str]) -> List[Tuple[str, Any]]:
        key = self._dialect()["system_key"]
        return [(key, s) for s in systems]

    def _metrics(self, path: str = "") -> str:
        return f"/metrics/{self.project_id}{path}"

    # ── traces ────────────────────────────────────────────────────────

    def _build_uql(self, f: StructuredFilter, root_filter: str = "") -> str:
        parts: List[str] = []
        if f.roots_only and root_filter:
            parts.append(root_filter)
        # Uptrace writes its own spans (service ``serve``) into the same
        # project; without a service the list is those, newest first, and the
        # agent's turns never reach page one. The same pin the log queries use
        # applies here unless the search names a service (#298).
        service = f.service or self.service_name
        if service:
            parts.append(f'where service_name = "{_esc(service)}"')
        if f.name_prefix:
            parts.append(f'where _name like "{_esc(f.name_prefix)}%"')
        if f.name_regex:
            parts.append(f'where _name like "{_esc(f.name_regex)}"')
        if f.status in ("error", "ok"):
            parts.append(f'where _status_code = "{f.status}"')
        if f.min_duration_ms:
            parts.append(f"where _duration >= {int(f.min_duration_ms)}ms")
        for k, v in f.attr_equals.items():
            parts.append(f'where {k.replace(".", "_")} = "{_esc(str(v))}"')
        if f.free_text:
            parts.append(f'where input_value contains "{_esc(f.free_text)}"')
        if f.raw and f.raw.strip():
            parts.append(f.raw.strip())
        return " | ".join(parts)

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        # Roots are kept client-side from each row's ``parentId`` (2.0 has no
        # filterable parent column), so fetch a wider page and keep one span
        # per trace, preferring the root.
        d = self._dialect()
        # 2.1 filters roots on the server (``_parent_id = ""``), so a page is
        # ``limit + 1`` rows and has_more is exact. 2.0 keeps roots client-side
        # and needs several rows per trace; a turn with more spans than that
        # can still hide older roots behind its own children (#298).
        root_filter = d.get("root_filter", "")
        server_roots = bool(f.roots_only and root_filter)
        # Rows are spans. Roots on 2.1 are one row per trace, so ``limit + 1``
        # makes has_more exact. Widened (the kind/tool filter) or on 2.0, a
        # trace contributes several rows (three ``tool.terminal`` spans of one
        # turn filled a two-row page and hid the other turn, #298): ask for
        # more, dedupe, and when the fetch came back full say so.
        rows_wanted = int(limit) + 1 if server_roots else int(limit) * 4 + 1
        params: List[Tuple[str, Any]] = [
            *self._systems(d["span_systems"]),
            ("query", self._build_uql(f, root_filter)),
            *d["sort"],
            ("limit", rows_wanted),
        ]
        end_ms = None
        if f.before_ns:
            end_ms = int(f.before_ns) // 1_000_000 + 1
        data = self._get(self._path("spans"), start_s, end_s, params, end_ms=end_ms)
        rows = (data.get("spans") if isinstance(data, dict) else None) or []
        traces: Dict[str, Dict[str, Any]] = {}
        for sp in rows:
            trace_id = sp.get("traceId")
            if not trace_id:
                continue
            is_root = not sp.get("parentId")
            if f.roots_only and not is_root:
                continue
            if trace_id in traces and not is_root:
                continue
            traces[trace_id] = _search_hit(sp, trace_id)
        page = trace_page(strictly_older_traces(list(traces.values()), f), limit)
        if not page["has_more"] and len(rows) >= rows_wanted and page["traces"]:
            # The fetch was cut by Uptrace, so older rows exist even though
            # they collapsed into fewer traces than the page holds.
            page["has_more"] = True
            page["next_before_ns"] = min(
                int(t.get("startTimeUnixNano") or 0) for t in page["traces"]
            )
        if page["has_more"] and not f.roots_only:
            # Widened rows are spans: a shown trace's older matching spans
            # would come back on the next page. Move the cursor below the
            # oldest fetched row of every trace on this page.
            shown = {t.get("traceID") for t in page["traces"]}
            oldest = [
                _row_start_ns(sp)
                for sp in rows
                if sp.get("traceId") in shown and _row_start_ns(sp) > 0
            ]
            if oldest:
                page["next_before_ns"] = min(
                    int(page["next_before_ns"] or 0) or min(oldest), *oldest
                )
        return page

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        url = self._api_url(self._path("trace", trace_id=trace_id), [])
        data = http_get_json(url, headers=self._headers(), timeout=20.0)
        out = _trace_to_otlp(data)
        n = sum(len(scope["spans"]) for b in out["batches"] for scope in b["scopeSpans"])
        if n == 0:
            raise BackendError(404, f"Trace {trace_id} not found in Uptrace", "not_found")
        out["span_count"] = n
        out["truncated"] = False
        return out

    # ── metrics (#194) ────────────────────────────────────────────────

    def _catalog(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        data = self._get(self._metrics(), start_s, end_s)
        out = [
            m
            for m in ((data.get("metrics") if isinstance(data, dict) else None) or [])
            if m.get("name")
        ]
        for m in out:
            if m.get("instrument"):
                self._instrument_cache[str(m["name"])] = str(m["instrument"])
        return out

    def _instrument_of(self, name: str, start_s: int, end_s: int) -> str:
        """The instrument kind of one metric, from the catalog seen so far
        (one request per chart, not two, #298)."""
        cached = self._instrument_cache.get(name)
        if cached:
            return cached
        self._catalog(start_s, end_s)
        return self._instrument_cache.get(name, "gauge")

    def metric_names(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        return [
            {"name": m["name"], "instrument": m.get("instrument")}
            for m in sorted(self._catalog(start_s, end_s), key=lambda m: m["name"])
        ]

    def metrics_query(
        self,
        name: str,
        start_s: int,
        end_s: int,
        bucket_s: int,
        group_by: Optional[str] = None,
        agg: str = "sum",
    ) -> Dict[str, Any]:
        instrument = self._instrument_of(name, start_s, end_s)
        cumulative = instrument == "counter" or (
            instrument == "histogram" and agg in ("sum", "count")
        )
        # A cumulative value is per process: without the instance in the
        # grouping Uptrace adds the processes' running totals together and the
        # increases of that sum are meaningless once two turns share an
        # interval (#298). The instance is a series key here, never a label.
        group_keys = [group_by.replace(".", "_")] if group_by else []
        if cumulative:
            group_keys.append(_INSTANCE_KEY)
        group_clause = ", ".join(group_keys) if group_keys else None
        expr = mql_for(instrument, agg, group_clause)
        label_key = group_by.replace(".", "_") if group_by else None

        def fetch(mql: str) -> List[Dict[str, Any]]:
            data = self._get(
                self._metrics("/timeseries"),
                start_s,
                end_s,
                [("metric", name), ("alias", "$m"), ("query", mql)],
            )
            return (data.get("timeseries") if isinstance(data, dict) else None) or []

        def ident_of(attrs: Dict[str, Any]) -> str:
            label = str(attrs.get(label_key, "—")) if label_key else "_"
            return f"{label}\x00{attrs.get(_INSTANCE_KEY, '')}" if cumulative else label

        points = []
        if instrument == "histogram" and agg == "count":
            # ``count($m)`` is the number of samples Uptrace holds, not the
            # histogram's observation count (verified on 2.1.0-beta.5: three
            # tool calls in one process read 1). ``avg($m)`` is sum over the
            # true count, so the count is ``sum($m) / avg($m)`` per series.
            expr = "sum($m) / avg($m)" + (f" group by {group_clause}" if group_clause else "")
            sums = {
                json.dumps(plain_attrs(s_.get("attrs")), sort_keys=True): s_
                for s_ in fetch(mql_for(instrument, "sum", group_clause))
            }
            for key, avg_series in (
                (json.dumps(plain_attrs(s_.get("attrs")), sort_keys=True), s_)
                for s_ in fetch(mql_for(instrument, "avg", group_clause))
            ):
                sum_series = sums.get(key)
                if not sum_series:
                    continue
                attrs = plain_attrs(avg_series.get("attrs"))
                ident = ident_of(attrs)
                sum_by_t = dict(zip(sum_series.get("time") or [], sum_series.get("value") or []))
                for ts_ms, avg in zip(avg_series.get("time") or [], avg_series.get("value") or []):
                    total = sum_by_t.get(ts_ms)
                    if avg is None or total is None or not avg:
                        continue
                    points.append((int(ts_ms) * 1_000_000, float(round(total / avg)), ident))
        else:
            for series in fetch(expr):
                attrs = plain_attrs(series.get("attrs"))
                ident = ident_of(attrs)
                for ts_ms, value in zip(series.get("time") or [], series.get("value") or []):
                    if value is None:
                        continue
                    points.append((int(ts_ms) * 1_000_000, float(value), ident))
        # Uptrace picks its own interval and, for a counter's ``$m`` and a
        # histogram's ``sum($m)`` / ``count($m)``, answers the CUMULATIVE value
        # at each interval end, forward-filled into later intervals (verified
        # on 2.1.0-beta.5: two turns of 2 calls read 4, 4 at 00:16 and 00:20;
        # ``delta($m)`` loses the first interval). The increases between its
        # points are what the chart wants, and a series that starts inside the
        # window counts its first value, exactly as for the other stores (#298).
        if cumulative:
            points = [
                (ts, v, ident.split("\x00", 1)[0])
                for ts, v, ident in counter_increases(
                    points, window_start_ns=start_s * 1_000_000_000
                )
            ]
        out = bucketize(points, start_s * 1_000_000_000, end_s * 1_000_000_000, bucket_s, "sum")
        out["agg"] = agg
        out["name"] = name
        out["instrument"] = instrument
        out["cumulative"] = cumulative
        out["mql"] = expr
        return out

    # ── logs (#194, #268) ─────────────────────────────────────────────

    def _log_clauses(self, f: LogFilter) -> List[str]:
        # Uptrace writes its own lines ("ClickHouse replica is back up", ...)
        # into the same project; keep the tab to the agent's service unless
        # the entry switched the pin off.
        clauses: List[str] = []
        if self.service_name:
            clauses.append(f'where service_name = "{_esc(self.service_name)}"')
        if f.trace_id:
            clauses.append(f'where _trace_id = "{_esc(f.trace_id)}"')
        if f.span_id:
            # A log record written inside a span is stored with that span as
            # its parent (the row's ``parentId``; UQL column ``_parent_id``,
            # verified on 2.1.0-beta.5: ``_span_id`` is "unsupported attr",
            # #298).
            clauses.append(f'where _parent_id = "{_esc(f.span_id)}"')
        if f.session:
            clauses.append(f'where hermes_session_id = "{_esc(f.session)}"')
        if f.logger:
            clauses.append(f'where otel_library_name = "{_esc(f.logger)}"')
        if f.event_name:
            clauses.append(f'where event_name = "{_esc(f.event_name)}"')
        elif f.events_only:
            clauses.append("where event_name exists")
        return clauses

    def logs_search(
        self, f: LogFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        d = self._dialect()
        params: List[Tuple[str, Any]] = self._systems(log_systems_for(f.min_level))
        # Uptrace floors the end bound to the SECOND (ClickHouse toDateTime),
        # so end at the next whole second above the cursor and ask for enough
        # rows to cover that second; strictly_older() then cuts the page at
        # the cursor itself.
        if f.before_ns:
            end_ms = (log_end_ns(end_s, f) // 1_000_000_000 + 1) * 1000
            fetch = int(limit) + 500
        else:
            end_ms, fetch = _ms(end_s), int(limit)
        params += [*d["sort"], ("limit", fetch), ("query", " | ".join(self._log_clauses(f)))]
        if f.text:
            params.append(("search", f.text))
        data = self._get(self._path("logs"), start_s, end_s, params, end_ms=end_ms)
        rows = [
            _log_record(sp) for sp in (data.get("spans") if isinstance(data, dict) else None) or []
        ]
        return strictly_older(rows, f)[: int(limit)]

    def loggers(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        d = self._dialect()
        params = [*self._systems(["log:all"]), *d["logger_params"]]
        if self.service_name:
            params.append(("query", f'where service_name = "{_esc(self.service_name)}"'))
        data = self._get(self._path("logger_values"), start_s, end_s, params)
        out = [
            {"logger": str(item.get("value")), "count": int(item.get("count") or 0)}
            for item in ((data.get("items") if isinstance(data, dict) else None) or [])
            if item.get("value")
        ]
        return sorted(out, key=lambda r: -r["count"])


# ── Helpers: response shape normalization ─────────────────────────────


def _attrs_dotted(raw: Any) -> Dict[str, Any]:
    return {_dotted(k): v for k, v in plain_attrs(raw).items()}


def _row_start_ns(sp: Dict[str, Any]) -> int:
    """Start of a raw span/log row in ns (``time`` is RFC 3339)."""
    try:
        return int(_search_hit(sp, str(sp.get("traceId") or "")).get("startTimeUnixNano") or 0)
    except Exception:
        return 0


def _search_hit(sp: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
    raw = plain_attrs(sp.get("attrs"))
    attrs: Dict[str, Any] = {_dotted(k): raw[k] for k in _CARD_ATTRS if k in raw}
    if sp.get("statusCode") in ("ok", "error"):
        attrs["status"] = sp["statusCode"]  # never Uptrace's ``unset``
    if raw.get("hermes_session_id"):
        attrs["hermes.session_id"] = raw["hermes_session_id"]
    if raw.get("hermes_turn_number") is not None:
        attrs["hermes.turn.number"] = raw["hermes_turn_number"]
    start_ns = _ns_from_ms(sp.get("time"))
    name = sp.get("name") or sp.get("displayName") or ""
    return {
        "traceID": trace_id,
        "rootServiceName": str(raw.get("service_name") or ""),
        "rootTraceName": name,
        "startTimeUnixNano": str(start_ns),
        "durationMs": int(float(sp.get("duration") or 0)),
        "spanSets": [
            {
                "spans": [
                    {
                        "spanID": sp.get("id"),
                        "name": name,
                        "attributes": otlp_attrs_from_dict(attrs),
                    }
                ],
                "matched": 1,
            }
        ],
    }


def _trace_to_otlp(data: Any) -> Dict[str, Any]:
    """A trace's span rows → OTLP batches, one per service.

    2.1's ``/traces/{p}/{id}`` nests a span's log records under ``logs``; the
    tree only needs the spans (the Logs sub-tab queries logs by trace id).
    """
    spans_raw = (data.get("spans") if isinstance(data, dict) else None) or []
    by_service: Dict[str, List[Dict[str, Any]]] = {}
    for sp in spans_raw:
        if not isinstance(sp, dict):
            continue
        raw = plain_attrs(sp.get("attrs"))
        start_ns = _ns_from_ms(sp.get("time"))
        duration_ns = _ns_from_ms(sp.get("duration") or 0)
        span = {
            "traceId": sp.get("traceId"),
            "spanId": sp.get("id"),
            "parentSpanId": sp.get("parentId") or None,
            "name": sp.get("name") or sp.get("displayName") or "",
            "kind": _KIND.get(str(sp.get("kind") or "internal"), 1),
            "startTimeUnixNano": str(start_ns),
            "endTimeUnixNano": str(start_ns + duration_ns if start_ns else 0),
            "attributes": otlp_attrs_from_dict({_dotted(k): v for k, v in raw.items()}),
            "status": otlp_status(sp.get("statusCode")),
        }
        by_service.setdefault(str(raw.get("service_name") or ""), []).append(span)
    return {
        "batches": [
            {
                "resource": {
                    "attributes": otlp_attrs_from_dict({"service.name": svc} if svc else {})
                },
                "scopeSpans": [{"spans": spans}],
            }
            for svc, spans in by_service.items()
        ]
    }


_KIND = {"internal": 1, "server": 2, "client": 3, "producer": 4, "consumer": 5}


def _log_record(sp: Dict[str, Any]) -> Dict[str, Any]:
    """One ``log:*`` row → the live store's record shape.

    Standalone log rows carry a synthetic trace id of their own; only a log
    written inside a span keeps the real one (its ``parentId`` is that span).
    """
    raw = plain_attrs(sp.get("attrs"))
    known = {
        "log_severity",
        "log_severity_number",
        "otel_library_name",
        "hermes_session_id",
        "event_name",
        "log_event_name",
        "service_name",
    }
    row = {
        "level": str(
            raw.get("log_severity") or str(sp.get("system") or "log:info").split(":")[-1]
        ).upper(),
        "severity_number": raw.get("log_severity_number"),
        "logger": str(raw.get("otel_library_name") or ""),
        "body": str(sp.get("displayName") or sp.get("name") or ""),
        "time_unix_nano": _ns_from_ms(sp.get("time")),
        "trace_id": None if sp.get("standalone") else (sp.get("traceId") or None),
        "span_id": None if sp.get("standalone") else (sp.get("parentId") or None),
        "session_id": raw.get("hermes_session_id") or None,
        "event_name": raw.get("event_name") or raw.get("log_event_name") or None,
    }
    return finish_log_row(row, {_dotted(k): v for k, v in raw.items() if k not in known})


def _raise_query_errors(data: Any) -> None:
    """Uptrace answers 200 with per-part ``error`` strings; surface them."""
    if not isinstance(data, dict):
        return
    errors = [
        p.get("error") for p in data.get("query") or [] if isinstance(p, dict) and p.get("error")
    ]
    if errors:
        raise BackendError(502, f"Uptrace rejected the query: {'; '.join(errors)}")
