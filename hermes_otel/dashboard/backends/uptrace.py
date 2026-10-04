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

import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib import parse as _urlparse

from . import register
from .base import (
    BackendAdapter,
    HTTPException,
    LogFilter,
    StructuredFilter,
    bucketize,
    finish_log_row,
    http_get_json,
    log_end_ns,
    otlp_attrs_from_dict,
    otlp_status,
    resolve_env_or_literal,
    rewrite_host_for_docker,
    strictly_older,
)
from .openobserve import _dotted

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
        self.service_name = str(cfg.get("service_name") or "hermes-agent")

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        base["project_id"] = self.project_id
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
        if not self.token:
            raise HTTPException(
                status_code=502,
                detail="Uptrace requires a user token. Set user_token or user_token_env.",
            )
        return {"Authorization": f"Bearer {self.token}"}

    def _dialect(self) -> Dict[str, Any]:
        """Which spelling of the API this server speaks; probed once per process.

        ``/logs/{p}/systems`` exists only in 2.1 — 2.0 answers it with the SPA's
        HTML (a "non-JSON" 502 here). A JSON error (validation, a slow
        ClickHouse) still means the route exists; an unreachable server is
        reported as such rather than guessed.
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
                if "non-JSON" in detail:
                    version = "2.0"
                elif "Backend returned" not in detail:
                    raise
            _DIALECT_CACHE[self.query_url] = version
        return _DIALECTS[version]

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
        return http_get_json(self._api_url(path, query), headers=self._headers(), timeout=20.0)

    def _systems(self, systems: Iterable[str]) -> List[Tuple[str, Any]]:
        key = self._dialect()["system_key"]
        return [(key, s) for s in systems]

    def _metrics(self, path: str = "") -> str:
        return f"/metrics/{self.project_id}{path}"

    # ── traces ────────────────────────────────────────────────────────

    def _build_uql(self, f: StructuredFilter) -> str:
        parts: List[str] = []
        if f.service:
            parts.append(f'where service_name = "{_esc(f.service)}"')
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
        params: List[Tuple[str, Any]] = [
            *self._systems(d["span_systems"]),
            ("query", self._build_uql(f)),
            *d["sort"],
            ("limit", int(limit) * (4 if f.roots_only else 1)),
        ]
        data = self._get(self._path("spans"), start_s, end_s, params)
        traces: Dict[str, Dict[str, Any]] = {}
        for sp in (data.get("spans") if isinstance(data, dict) else None) or []:
            trace_id = sp.get("traceId")
            if not trace_id:
                continue
            is_root = not sp.get("parentId")
            if f.roots_only and not is_root:
                continue
            if trace_id in traces and not is_root:
                continue
            traces[trace_id] = _search_hit(sp, trace_id)
            if len(traces) >= limit and not f.roots_only:
                break
        out = sorted(traces.values(), key=lambda t: int(t["startTimeUnixNano"]), reverse=True)
        return {"traces": out[: int(limit)]}

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        url = self._api_url(self._path("trace", trace_id=trace_id), [])
        data = http_get_json(url, headers=self._headers(), timeout=20.0)
        return _trace_to_otlp(data)

    # ── metrics (#194) ────────────────────────────────────────────────

    def _catalog(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        data = self._get(self._metrics(), start_s, end_s)
        return [
            m
            for m in ((data.get("metrics") if isinstance(data, dict) else None) or [])
            if m.get("name")
        ]

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
        instrument = next(
            (
                str(m.get("instrument") or "gauge")
                for m in self._catalog(start_s, end_s)
                if m["name"] == name
            ),
            "gauge",
        )
        expr = mql_for(instrument, agg, group_by)
        data = self._get(
            self._metrics("/timeseries"),
            start_s,
            end_s,
            [("metric", name), ("alias", "$m"), ("query", expr)],
        )
        _raise_query_errors(data)
        label_key = group_by.replace(".", "_") if group_by else None
        points = []
        for series in (data.get("timeseries") if isinstance(data, dict) else None) or []:
            attrs = plain_attrs(series.get("attrs"))
            label = str(attrs.get(label_key, "—")) if label_key else "_"
            for ts_ms, value in zip(series.get("time") or [], series.get("value") or []):
                if value is None:
                    continue
                points.append((int(ts_ms) * 1_000_000, float(value), label))
        # Uptrace picks its own interval; folding its points with ``sum`` onto
        # the requested grid keeps totals exact for counters and histograms'
        # sum/count, which is what the dashboard charts.
        out = bucketize(points, start_s * 1_000_000_000, end_s * 1_000_000_000, bucket_s, "sum")
        out["agg"] = agg
        out["name"] = name
        out["instrument"] = instrument
        out["mql"] = expr
        return out

    # ── logs (#194, #268) ─────────────────────────────────────────────

    def _log_clauses(self, f: LogFilter) -> List[str]:
        # Uptrace writes its own lines ("ClickHouse replica is back up", ...)
        # into the same project; keep the tab to the agent's service (the
        # entry's ``service_name``, default ``hermes-agent``).
        clauses: List[str] = [f'where service_name = "{_esc(self.service_name)}"']
        if f.trace_id:
            clauses.append(f'where _trace_id = "{_esc(f.trace_id)}"')
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
        params = [
            *self._systems(["log:all"]),
            *d["logger_params"],
            ("query", f'where service_name = "{_esc(self.service_name)}"'),
        ]
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


def _search_hit(sp: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
    raw = plain_attrs(sp.get("attrs"))
    attrs: Dict[str, Any] = {_dotted(k): raw[k] for k in _CARD_ATTRS if k in raw}
    if sp.get("statusCode"):
        attrs["status"] = sp["statusCode"]
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
        raise HTTPException(
            status_code=502, detail=f"Uptrace rejected the query: {'; '.join(errors)}"
        )
