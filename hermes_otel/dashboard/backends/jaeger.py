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
plugin's own ``resource_attributes.service.name``, else ``hermes-agent``.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib import parse as _urlparse

from . import default_service_name, register
from .base import (
    BackendAdapter,
    BackendError,
    StructuredFilter,
    bearer,
    http_get_json,
    not_found,
    otlp_status,
    parse_kv_tokens,
    query_origin,
    register_cache,
    resolve_env_or_literal,
    root_of,
    span_matches,
)
from .paging import strictly_older_traces, trace_end_bound, trace_page
from .shapes import (
    card_attrs,
    card_row,
    group_by_service,
    otlp_attrs_to_dict,
    otlp_batches,
    otlp_span,
    span_count_of,
    to_int,
)

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


def _rfc3339(ns: int) -> str:
    """``query.startTimeMin`` / ``Max`` take RFC 3339 timestamps."""
    secs, rem = divmod(int(ns), 1_000_000_000)
    base = _dt.datetime.fromtimestamp(secs, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{rem:09d}Z"


def _parent_of(span: Dict[str, Any]) -> Optional[str]:
    """The parent span id of a classic (v1) span: its ``CHILD_OF`` reference."""
    for r in span.get("references") or []:
        if isinstance(r, dict) and r.get("refType") == "CHILD_OF" and r.get("spanID"):
            return r["spanID"]
    return None


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


def _v3_spans_by_trace(data: Any) -> Dict[str, List[Dict[str, Any]]]:
    """A v3 search answer (``result.resourceSpans``) as spans per trace id,
    each span carrying its resource's service under ``_service``."""
    result = data.get("result") if isinstance(data, dict) else None
    resource_spans = (result or {}).get("resourceSpans") if isinstance(result, dict) else None
    by_trace: Dict[str, List[Dict[str, Any]]] = {}
    for rs in resource_spans or []:
        if not isinstance(rs, dict):
            continue
        resource = otlp_attrs_to_dict((rs.get("resource") or {}).get("attributes"))
        service = str(resource.get("service.name") or "")
        for scope in rs.get("scopeSpans") or []:
            for sp in (scope or {}).get("spans") or []:
                if isinstance(sp, dict) and sp.get("traceId"):
                    by_trace.setdefault(str(sp["traceId"]), []).append({**sp, "_service": service})
    return by_trace


def _pick_card(
    f: StructuredFilter,
    spans: List[Dict[str, Any]],
    *,
    span_id: Callable[[Dict[str, Any]], Any],
    parent_id: Callable[[Dict[str, Any]], Any],
    name: Callable[[Dict[str, Any]], str],
    start: Callable[[Dict[str, Any]], int],
    duration_ns: Callable[[Dict[str, Any]], int],
    attrs_of: Callable[[Dict[str, Any]], Dict[str, Any]],
    equals: Dict[str, Any],
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """The span a trace's card is built from, with its attributes, or ``None``
    when the trace has no card under the filter.

    Jaeger's ``tags=`` and ``operation=`` match ANY span of a trace, so with
    roots-only the ROOT itself must satisfy the predicates (#295), and an
    orphan (its real root outside the window) is no root. Widened by the
    kind filter, the card is the first span carrying the prefix, as the other
    backends show it, with that span's own attributes, start and duration.
    """
    root = root_of(spans, span_id, parent_id)
    if root is None:
        return None
    if f.roots_only:
        if parent_id(root):
            return None
        attrs = attrs_of(root)
        ok = span_matches(
            f, name=name(root), attrs=attrs, duration_ns=duration_ns(root), equals=equals
        )
        return (root, attrs) if ok else None
    if f.name_prefix:
        card = next(
            (sp for sp in sorted(spans, key=start) if name(sp).startswith(f.name_prefix)), None
        )
        return (card, attrs_of(card)) if card is not None else None
    return root, attrs_of(root)


@register
class JaegerAdapter(BackendAdapter):
    handles = frozenset({"jaeger"})
    query_lang_label = "Jaeger tags (key=value)"
    default_query_port = _DEFAULT_JAEGER_QUERY_PORT
    default_query_path = ""
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
        # The endpoint's own port stands in for a missing ``query_port``: the
        # stores that embed this API serve it on their OTLP port.
        self.query_url = query_origin(cfg, self.default_query_port, endpoint_port="fallback")
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

    def _api(self) -> str:
        """``v1`` or ``v3``: the entry's pin, else probed once per query URL.
        The classic ``/api/services`` wins when it answers (Jaeger 1.x, whose
        ``all-in-one`` also exposes a ``/api/v3`` gateway with snake_case
        parameters, verified 2026-10-09); only a server without it (Jaeger
        v2, where the classic routes are 404) is spoken to as v3. An
        unreachable or unauthenticated server is reported, not guessed."""
        if self.query_api != "auto":
            return self.query_api
        cached = _API_PROBE_CACHE.get(self.api_base)
        if cached:
            return cached
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
        return api

    def _build_query_v3(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> List[Tuple[str, str]]:
        """The v3 grammar (verified on Jaeger 2.21.0): camelCase keys,
        RFC 3339 bounds, ``searchDepth`` for the limit, ``durationMin`` as
        ``<n>ms`` and the tag map JSON-encoded in ``query.attributes``
        (``attributes[k]=v`` and ``attributes.k=v`` are silently ignored)."""
        params: List[Tuple[str, str]] = [
            ("query.serviceName", f.service or self.default_service),
            ("query.startTimeMin", _rfc3339(int(start_s) * 1_000_000_000)),
            ("query.startTimeMax", _rfc3339(trace_end_bound(f, end_s, "ns"))),
            ("query.searchDepth", str(self._fetch_limit(limit))),
        ]
        if f.name_regex:
            params.append(("query.operationName", f.name_regex))
        if f.min_duration_ms and f.min_duration_ms > 0:
            params.append(("query.durationMin", f"{int(f.min_duration_ms)}ms"))
        tags = self._tags(f)
        if tags:
            params.append(("query.attributes", json.dumps(tags)))
        return params

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
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "service": f.service or self.default_service,
            # One beyond the page so has_more is exact (or the over-fetch size).
            "limit": self._fetch_limit(limit),
            "start": int(start_s) * 1_000_000,
            "end": trace_end_bound(f, end_s, "us"),
        }
        if f.name_regex:
            params["operation"] = f.name_regex
        if f.min_duration_ms and f.min_duration_ms > 0:
            params["minDuration"] = f"{int(f.min_duration_ms)}ms"
        tags = self._tags(f)
        if tags:
            params["tags"] = json.dumps(tags)
        return params

    # ── Shape translation ────────────────────────────────────────────

    def _span_tags_as_dict(self, span: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for tag in span.get("tags") or []:
            if isinstance(tag, dict) and tag.get("key"):
                out[tag["key"]] = _tag_value(tag)
        return out

    def _service_name_for_span(self, span: Dict[str, Any], processes: Dict[str, Any]) -> str:
        pid = span.get("processID")
        if pid and isinstance(processes, dict):
            proc = processes.get(pid)
            if isinstance(proc, dict):
                return proc.get("serviceName") or ""
        return ""

    @staticmethod
    def _status_of(attrs: Dict[str, Any]) -> Dict[str, Any]:
        """OTLP status from the ``otel.status_code`` / ``otel.status_description``
        tags Jaeger keeps for OTLP spans, else the ``error`` tag."""
        code = attrs.get("otel.status_code")
        message = str(attrs.get("otel.status_description") or "")
        if isinstance(code, str) and code:
            return otlp_status(code, message)
        return otlp_status("error" if attrs.get("error") else "ok", message)

    # ── Public API ───────────────────────────────────────────────────

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        rows = self._search_v3(f, start_s, end_s, limit) if self._api() == "v3" else None
        if rows is None:
            rows = self._search_v1(f, start_s, end_s, limit)
        return trace_page(strictly_older_traces(rows, f), limit)

    def _search_v1(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        params = self._build_query(f, start_s, end_s, limit)
        url = f"{self.api_base}/api/traces?{_urlparse.urlencode(params)}"
        data = http_get_json(url, headers=self._headers(), timeout=15.0)
        items = data.get("data") if isinstance(data, dict) else None
        equals = self._tags(f)
        rows: List[Dict[str, Any]] = []
        for t in items if isinstance(items, list) else []:
            if not isinstance(t, dict):
                continue
            trace_id = t.get("traceID")
            spans = [sp for sp in t.get("spans") or [] if isinstance(sp, dict)]
            if not trace_id or not spans:
                continue
            picked = _pick_card(
                f,
                spans,
                span_id=lambda sp: sp.get("spanID"),
                parent_id=_parent_of,
                name=lambda sp: str(sp.get("operationName") or ""),
                start=lambda sp: to_int(sp.get("startTime")),
                duration_ns=lambda sp: to_int(sp.get("duration")) * 1000,
                attrs_of=self._span_tags_as_dict,
                equals=equals,
            )
            if picked is None:
                continue
            card, attrs = picked
            name = card.get("operationName") or ""
            rows.append(
                card_row(
                    trace_id,
                    span_id=card.get("spanID"),
                    name=name,
                    attrs=card_attrs(attrs, name, self._status_of(attrs).get("code") == 2),
                    start_ns=to_int(card.get("startTime")) * 1000,
                    duration_ms=to_int(card.get("duration")) // 1000,
                    service=self._service_name_for_span(card, t.get("processes") or {}),
                    span_count=len(spans),
                )
            )
        return rows

    def _search_v3(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        """The v3 answer is OTLP JSON: its spans go straight to cards."""
        params = self._build_query_v3(f, start_s, end_s, limit)
        url = f"{self.api_base}/api/v3/traces?{_urlparse.urlencode(params)}"
        try:
            data = http_get_json(url, headers=self._headers(), timeout=15.0)
        except BackendError as exc:
            # Jaeger v2 answers an empty search with 404 "No traces found".
            if exc.kind != "not_found":
                raise
            data = {}

        def span_ns(sp: Dict[str, Any]) -> Tuple[int, int]:
            start = to_int(sp.get("startTimeUnixNano"))
            return start, max(0, to_int(sp.get("endTimeUnixNano")) - start)

        equals = self._tags(f)
        rows: List[Dict[str, Any]] = []
        for trace_id, spans in _v3_spans_by_trace(data).items():
            picked = _pick_card(
                f,
                spans,
                span_id=lambda sp: sp.get("spanId"),
                parent_id=lambda sp: sp.get("parentSpanId"),
                name=lambda sp: str(sp.get("name") or ""),
                start=lambda sp: span_ns(sp)[0],
                duration_ns=lambda sp: span_ns(sp)[1],
                attrs_of=_v3_attrs,
                equals=equals,
            )
            if picked is None:
                continue
            card, attrs = picked
            name = card.get("name") or ""
            start_ns, dur_ns = span_ns(card)
            rows.append(
                card_row(
                    trace_id,
                    span_id=card.get("spanId"),
                    name=name,
                    attrs=card_attrs(attrs, name, self._status_of(attrs).get("code") == 2),
                    start_ns=start_ns,
                    duration_ms=dur_ns // 1_000_000,
                    service=card.get("_service") or "",
                    span_count=len(spans),
                )
            )
        return rows

    def trace_url(self, trace_id: str) -> Optional[str]:
        return f"{self.query_url}/trace/{trace_id}"

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        if self._api() == "v3":
            # OTLP JSON with hex ids: the detail's own shape, passed through.
            url = f"{self.api_base}/api/v3/traces/{trace_id}"
            try:
                data = http_get_json(url, headers=self._headers(), timeout=20.0)
            except BackendError as exc:
                if exc.kind == "not_found":
                    raise not_found(trace_id, "Jaeger")
                raise
            result = data.get("result") if isinstance(data, dict) else None
            batches = (result or {}).get("resourceSpans") if isinstance(result, dict) else None
            batches = [b for b in (batches or []) if isinstance(b, dict)]
            n = span_count_of(batches)
            if n == 0:
                raise not_found(trace_id, "Jaeger")
            return {"batches": batches, "span_count": n, "truncated": False}
        url = f"{self.api_base}/api/traces/{trace_id}"
        data = http_get_json(url, headers=self._headers(), timeout=20.0)
        items = data.get("data") if isinstance(data, dict) else None
        if not items:
            raise not_found(trace_id, "Jaeger")
        trace = items[0] if isinstance(items, list) else items
        processes = trace.get("processes") or {}
        spans = [sp for sp in trace.get("spans") or [] if isinstance(sp, dict)]

        def one(sp: Dict[str, Any]) -> Dict[str, Any]:
            attrs = self._span_tags_as_dict(sp)
            start_us = to_int(sp.get("startTime"))
            return otlp_span(
                trace_id,
                sp.get("spanID"),
                _parent_of(sp),
                sp.get("operationName"),
                start_ns=start_us * 1000,
                end_ns=(start_us + to_int(sp.get("duration"))) * 1000,
                attrs=attrs,
                status=self._status_of(attrs),
            )

        by_service = group_by_service(spans, lambda sp: self._service_name_for_span(sp, processes))
        batches = otlp_batches({svc: [one(sp) for sp in sps] for svc, sps in by_service.items()})
        return {"batches": batches, "span_count": len(spans), "truncated": False}
