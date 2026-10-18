"""Jaeger adapter — classic HTTP query API at ``/api/traces`` (Jaeger v1) and the
v3 query API at ``/api/v3/...`` that Jaeger v2 serves on the same port (#245).

Self-hosted Jaeger usually runs unauthenticated on localhost. Cloud
offerings (Grafana Cloud Traces, etc.) put an API gateway in front;
the adapter supports a bearer token via ``api_key`` / ``api_key_env``
when present.

Jaeger search filters by ``service`` + ``tags``; there is no TraceQL
equivalent. Structured filters map directly, and the raw query field
accepts additional ``key=value`` pairs that become more tags. ``tags=``
matches ANY span of a trace, so with ``roots_only`` the adapter re-checks
the filter against the root span of every returned trace (#295).

Every query names a service: ``service_name`` on the entry, else the
plugin's own ``resource_attributes.service.name``, else the service the
plugin reports for the viewed profile (``hermes-agent``, or the profile's
name with ``service_name_from_profile``; see ``default_service_name``).

The two APIs ask for the same seven facts under two spellings and answer in
two shapes; :data:`_JAEGER_DIALECTS` holds both spellings and the decoder
of each shape, and the adapter speaks whichever the probe (or the entry's
``query_api`` pin) selects.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple
from urllib import parse as _urlparse

from . import default_service_name, register
from .base import (
    BackendAdapter,
    BackendError,
    StructuredFilter,
    bearer,
    http_get_json,
    not_found,
    parse_kv_tokens,
    query_origin,
    register_cache,
    resolve_env_or_literal,
)
from .paging import strictly_older_traces, trace_end_bound, trace_page
from .shapes import (
    SpanRow,
    card_from_trace,
    otlp_attrs_to_dict,
    otlp_status,
    span_count_of,
    to_int,
    trace_detail,
)
from .timeconv import ns_from_s, ns_to_rfc3339

_DEFAULT_JAEGER_QUERY_PORT = 16686


def _tag_value(tag: Dict[str, Any]) -> Any:
    """Pull the Python-shaped value out of a Jaeger tag entry."""
    v = tag.get("value")
    t = (tag.get("type") or "").lower()
    if t in ("int64", "int"):
        try:
            return int(v)
        except Exception:
            return v
    if t in ("float64", "double", "float"):
        try:
            return float(v)
        except Exception:
            return v
    if t in ("bool", "boolean"):
        return bool(v)
    return v


# Jaeger v2 serves only ``/api/v3/...`` on the query port (the classic routes
# are 404, #245). The API is probed once per query URL; ``query_api: v1|v3``
# on the entry pins it.
_API_PROBE_CACHE: Dict[str, str] = register_cache({})


def _parent_of(span: Dict[str, Any]) -> Optional[str]:
    """The parent span id of a classic (v1) span: its ``CHILD_OF`` reference."""
    for r in span.get("references") or []:
        if isinstance(r, dict) and r.get("refType") == "CHILD_OF" and r.get("spanID"):
            return r["spanID"]
    return None


def _service_name_for_span(span: Dict[str, Any], processes: Dict[str, Any]) -> str:
    """The service of a classic (v1) span: its process's ``serviceName``."""
    pid = span.get("processID")
    if pid and isinstance(processes, dict):
        proc = processes.get(pid)
        if isinstance(proc, dict):
            return proc.get("serviceName") or ""
    return ""


def _status_of(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """OTLP status from the ``otel.status_code`` / ``otel.status_description``
    tags Jaeger keeps for OTLP spans, else the ``error`` tag."""
    code = attrs.get("otel.status_code")
    message = str(attrs.get("otel.status_description") or "")
    if isinstance(code, str) and code:
        return otlp_status(code, message)
    return otlp_status("error" if attrs.get("error") else "ok", message)


# ── the classic (v1) answer ─────────────────────────────────────────────


def _v1_rows(a: "JaegerAdapter", data: Any) -> Dict[str, List[SpanRow]]:
    """A classic search answer (``data: [{traceID, spans, processes}]``) as
    rows per trace id, through ``a._row`` (a store with its own tag encoding
    overrides the tag decoding, Victoria's typed strings)."""
    items = data.get("data") if isinstance(data, dict) else None
    by_trace: Dict[str, List[SpanRow]] = {}
    for t in items if isinstance(items, list) else []:
        if not isinstance(t, dict):
            continue
        trace_id = t.get("traceID")
        spans = [sp for sp in t.get("spans") or [] if isinstance(sp, dict)]
        if trace_id and spans:
            processes = t.get("processes") or {}
            by_trace[trace_id] = [a._row(sp, trace_id, processes) for sp in spans]
    return by_trace


def _v1_detail(a: "JaegerAdapter", data: Any, trace_id: str) -> Optional[Dict[str, Any]]:
    """A classic trace answer (one trace in ``data``) as the detail; ``None``
    when ``data`` is empty, how classic Jaeger says "no such trace"."""
    rows = _v1_rows(a, data)
    return trace_detail(next(iter(rows.values()))) if rows else None


# ── the v3 (OTLP JSON) answer ───────────────────────────────────────────


def _v3_attrs(span: Dict[str, Any]) -> Dict[str, Any]:
    """A v3 (OTLP JSON) span's attributes as the tag dict the v1 path yields:
    the attributes plus ``otel.status_code`` / ``error`` /
    ``otel.status_description`` from the OTLP status, the tags Jaeger keeps
    for an OTLP span, so ``_status_of`` and the tag re-check read both APIs
    alike."""
    attrs = otlp_attrs_to_dict(span.get("attributes"))
    status = span.get("status") or {}
    code = status.get("code")
    if code == 2 or code == "STATUS_CODE_ERROR":
        attrs["otel.status_code"] = "ERROR"
        attrs["error"] = True
    elif code == 1 or code == "STATUS_CODE_OK":
        attrs["otel.status_code"] = "OK"
    if status.get("message"):
        attrs["otel.status_description"] = str(status["message"])
    return attrs


def _v3_row(span: Dict[str, Any], service: str) -> SpanRow:
    """A v3 (OTLP JSON) span as a row, its service from the resource."""
    attrs = _v3_attrs(span)
    return SpanRow(
        str(span["traceId"]),
        span.get("spanId"),
        span.get("parentSpanId"),
        str(span.get("name") or ""),
        start_ns=to_int(span.get("startTimeUnixNano")),
        end_ns=to_int(span.get("endTimeUnixNano")),
        attrs=attrs,
        service=service,
        status=_status_of(attrs),
    )


def _v3_resource_spans(data: Any) -> List[Dict[str, Any]]:
    """The ``result.resourceSpans`` batches of a v3 answer."""
    result = data.get("result") if isinstance(data, dict) else None
    batches = (result or {}).get("resourceSpans") if isinstance(result, dict) else None
    return [b for b in (batches or []) if isinstance(b, dict)]


def _v3_rows(a: "JaegerAdapter", data: Any) -> Dict[str, List[SpanRow]]:
    """A v3 search answer as rows per trace id."""
    by_trace: Dict[str, List[SpanRow]] = {}
    for rs in _v3_resource_spans(data):
        resource = otlp_attrs_to_dict((rs.get("resource") or {}).get("attributes"))
        service = str(resource.get("service.name") or "")
        for scope in rs.get("scopeSpans") or []:
            for sp in (scope or {}).get("spans") or []:
                if isinstance(sp, dict) and sp.get("traceId"):
                    by_trace.setdefault(str(sp["traceId"]), []).append(_v3_row(sp, service))
    return by_trace


def _v3_detail(a: "JaegerAdapter", data: Any, trace_id: str) -> Optional[Dict[str, Any]]:
    """A v3 trace answer is OTLP JSON with hex ids, the detail's own shape:
    its batches pass through. ``None`` when there are no spans."""
    batches = _v3_resource_spans(data)
    n = span_count_of(batches)
    if n == 0:
        return None
    return {"batches": batches, "span_count": n, "truncated": False}


# The facts a search sends, in the order they are sent; ``params`` below
# spells each one per API (the last three only when the filter has them).
_SEARCH_FACTS = ("service", "limit", "start", "end", "operation", "min_duration", "tags")

# How each query API spells the same search (the model Uptrace uses, #517):
# the routes, the parameter names, a time bound (v1: µs; v3: RFC 3339, verified
# on Jaeger 2.21.0), whether a 404 means "nothing there" (Jaeger v2 answers an
# empty search and an unknown trace with 404; classic Jaeger answers both with
# an empty ``data``, so its 404 is a wrong URL, reported as it came), and the
# decoders of a search answer (rows per trace id) and of a trace answer (the
# detail). v3 takes the tag map JSON-encoded in ``query.attributes``
# (``attributes[k]=v`` is silently ignored) and ``searchDepth`` for the limit.
_JAEGER_DIALECTS: Dict[str, Dict[str, Any]] = {
    "v1": {
        "search": "/api/traces",
        "trace": "/api/traces/{trace_id}",
        "params": ("service", "limit", "start", "end", "operation", "minDuration", "tags"),
        "time": lambda ns: ns // 1000,
        "not_found_is_empty": False,
        "rows_of": _v1_rows,
        "detail": _v1_detail,
    },
    "v3": {
        "search": "/api/v3/traces",
        "trace": "/api/v3/traces/{trace_id}",
        "params": (
            "query.serviceName",
            "query.searchDepth",
            "query.startTimeMin",
            "query.startTimeMax",
            "query.operationName",
            "query.durationMin",
            "query.attributes",
        ),
        "time": ns_to_rfc3339,
        "not_found_is_empty": True,
        "rows_of": _v3_rows,
        "detail": _v3_detail,
    },
}


@register
class JaegerAdapter(BackendAdapter):
    handles = frozenset({"jaeger"})
    query_lang_label = "Jaeger tags (key=value)"
    default_query_port = _DEFAULT_JAEGER_QUERY_PORT
    default_query_path = ""
    # How ``query_origin`` treats the endpoint's port when ``query_port`` is
    # unset: Jaeger's query API is never on its OTLP port, so ``16686``
    # (#605); the stores that embed this API on their OTLP port say
    # ``"fallback"``.
    query_endpoint_port = "ignore"
    # How many traces a search asks the server for. Jaeger returns the newest
    # ``limit`` traces, so one beyond the page is enough (``None``); a store
    # whose ``limit`` keeps arbitrary traces sets a fetch size and the page is
    # cut here (GreptimeDB, #363). ``search_fetch`` on the entry overrides.
    default_search_fetch: Optional[int] = None
    raw_placeholder = "http.status_code=500 error=true"
    filter_support = {
        "service": "server",
        "name": "client",  # ``operation=`` is exact; a prefix is checked on the rows
        "model": "server",
        "session": "server",
        "tool": "server",
        "min_duration": "server",
        "status_error": "server",
        "status_ok": "none",  # Jaeger has no negative tag search
        "free_text": "none",
        "raw": "server",
        "roots_only": "client",
    }

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        self.query_url = query_origin(
            cfg, self.default_query_port, endpoint_port=self.query_endpoint_port
        )
        # Where ``/api/...`` hangs: the origin for Jaeger itself, a prefix for
        # stores that embed a Jaeger-compatible API (GreptimeDB's
        # ``/v1/jaeger``, #363). ``query_path`` on the entry overrides.
        path = cfg.get("query_path")
        path = (path if isinstance(path, str) else self.default_query_path).strip("/")
        self.api_base = f"{self.query_url}/{path}" if path else self.query_url
        # Optional bearer for cloud-hosted Jaeger / authenticated proxy.
        self.api_key = resolve_env_or_literal(cfg, "api_key", "api_key_env")
        self.default_service = default_service_name(cfg)
        # ``query_api``: ``v1`` (classic ``/api/traces``), ``v3`` (Jaeger v2's
        # ``/api/v3``), or ``auto`` (probed once per query URL).
        api = str(cfg.get("query_api") or "auto").strip().lower()
        self.query_api = api if api in ("v1", "v3") else "auto"
        try:
            fetch = cfg.get("search_fetch")
            self.search_fetch = int(fetch) if fetch else self.default_search_fetch
        except (TypeError, ValueError):
            self.search_fetch = self.default_search_fetch

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["default_service"] = self.default_service
        base["query_api"] = (
            self.query_api
            if self.query_api != "auto"
            else _API_PROBE_CACHE.get(self.api_base, "auto")
        )
        return base

    def _dialect(self) -> Dict[str, Any]:
        """The :data:`_JAEGER_DIALECTS` entry this server speaks: the entry's
        pin, else probed once per query URL. The classic ``/api/services``
        wins when it answers (Jaeger 1.x, whose ``all-in-one`` also exposes a
        ``/api/v3`` gateway with snake_case parameters, verified 2026-10-09);
        only a server without it (Jaeger v2, where the classic routes are 404)
        is spoken to as v3. An unreachable or unauthenticated server is
        reported, not guessed."""
        if self.query_api != "auto":
            return _JAEGER_DIALECTS[self.query_api]
        cached = _API_PROBE_CACHE.get(self.api_base)
        if cached:
            return _JAEGER_DIALECTS[cached]
        api = "v1"
        try:
            data = http_get_json(
                f"{self.api_base}/api/services", headers=self._headers(), timeout=10.0
            )
            classic = isinstance(data, dict) and "data" in data
        except BackendError as exc:
            if exc.kind != "not_found":
                raise
            classic = False
        if not classic:
            try:
                data = http_get_json(
                    f"{self.api_base}/api/v3/services", headers=self._headers(), timeout=10.0
                )
                if isinstance(data, dict) and "services" in data:
                    api = "v3"
            except BackendError as exc:
                if exc.kind != "not_found":
                    raise
        _API_PROBE_CACHE[self.api_base] = api
        return _JAEGER_DIALECTS[api]

    def _headers(self) -> Dict[str, str]:
        return bearer(self.api_key)

    def _fetch_limit(self, limit: int) -> int:
        """Traces to ask for: one beyond the page, or the over-fetch size when
        the server's ``limit`` does not keep the newest traces."""
        if self.search_fetch:
            return max(int(limit) + 1, int(self.search_fetch))
        return int(limit) + 1

    # ── Filter translation ───────────────────────────────────────────

    def _tags(self, f: StructuredFilter) -> Dict[str, Any]:
        tags: Dict[str, Any] = dict(f.attr_equals)
        tags.update(parse_kv_tokens(f.raw))
        if f.status == "error":
            tags["error"] = "true"
        return tags

    def _build_query(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> List[Tuple[str, Any]]:
        """The search's parameters in the dialect's spelling and order."""
        d = self._dialect()
        facts: Dict[str, Any] = {
            "service": f.service or self.default_service,
            # One beyond the page so has_more is exact (or the over-fetch size).
            "limit": self._fetch_limit(limit),
            "start": d["time"](ns_from_s(start_s)),
            "end": d["time"](trace_end_bound(f, end_s, "ns")),
        }
        if f.name_regex:
            facts["operation"] = f.name_regex
        if f.min_duration_ms and f.min_duration_ms > 0:
            facts["min_duration"] = f"{int(f.min_duration_ms)}ms"
        tags = self._tags(f)
        if tags:
            facts["tags"] = json.dumps(tags)
        return [
            (key, facts[fact]) for fact, key in zip(_SEARCH_FACTS, d["params"]) if fact in facts
        ]

    # ── Shape translation ────────────────────────────────────────────

    @staticmethod
    def _span_tags_as_dict(span: Dict[str, Any]) -> Dict[str, Any]:
        """A classic (v1) span's typed tags as a dict. The one hook of the v1
        decoding: a store with its own tag encoding overrides it (Victoria's
        every-tag-is-a-string API)."""
        out: Dict[str, Any] = {}
        for tag in span.get("tags") or []:
            if isinstance(tag, dict) and tag.get("key"):
                out[tag["key"]] = _tag_value(tag)
        return out

    def _row(self, span: Dict[str, Any], trace_id: Any, processes: Dict[str, Any]) -> SpanRow:
        """A classic (v1) span as a row: tags as a dict, µs to ns, the
        service from the trace's ``processes``."""
        attrs = self._span_tags_as_dict(span)
        start_us = to_int(span.get("startTime"))
        return SpanRow(
            trace_id,
            span.get("spanID"),
            _parent_of(span),
            str(span.get("operationName") or ""),
            start_ns=start_us * 1000,
            end_ns=(start_us + to_int(span.get("duration"))) * 1000,
            attrs=attrs,
            service=_service_name_for_span(span, processes),
            status=_status_of(attrs),
        )

    # ── Public API ───────────────────────────────────────────────────

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        d = self._dialect()
        params = self._build_query(f, start_s, end_s, limit)
        url = f"{self.api_base}{d['search']}?{_urlparse.urlencode(params)}"
        try:
            data = http_get_json(url, headers=self._headers(), timeout=15.0)
        except BackendError as exc:
            if exc.kind != "not_found" or not d["not_found_is_empty"]:
                raise
            data = {}
        equals = self._tags(f)
        cards = [
            card_from_trace(f, rows, equals=equals) for rows in d["rows_of"](self, data).values()
        ]
        return trace_page(strictly_older_traces([c for c in cards if c is not None], f), limit)

    def trace_url(self, trace_id: str) -> Optional[str]:
        return f"{self.query_url}/trace/{trace_id}"

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        d = self._dialect()
        url = f"{self.api_base}{d['trace'].format(trace_id=trace_id)}"
        try:
            data = http_get_json(url, headers=self._headers(), timeout=20.0)
        except BackendError as exc:
            if exc.kind == "not_found" and d["not_found_is_empty"]:
                raise not_found(trace_id, "Jaeger")
            raise
        detail = d["detail"](self, data, trace_id)
        if detail is None:
            raise not_found(trace_id, "Jaeger")
        return detail
