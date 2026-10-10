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
from typing import Any, Dict, List, Optional, Tuple
from urllib import parse as _urlparse

from . import default_service_name, register
from .base import (
    BackendAdapter,
    BackendError,
    StructuredFilter,
    http_get_json,
    otlp_attrs_from_dict,
    otlp_status,
    parse_kv_tokens,
    resolve_env_or_literal,
    rewrite_host_for_docker,
    strictly_older_traces,
    trace_page,
)

_DEFAULT_JAEGER_QUERY_PORT = 16686

_CARD_TAGS = frozenset(
    {
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
    }
)


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


def _int(value: Any) -> int:
    """A Jaeger numeric field (``startTime``, ``duration``), 0 when malformed."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


# Jaeger v2 serves only ``/api/v3/...`` on the query port (the classic routes
# are 404, #245). The API is probed once per query URL; ``query_api: v1|v3``
# on the entry pins it.
_API_PROBE_CACHE: Dict[str, str] = {}


def _rfc3339(ns: int) -> str:
    """``query.startTimeMin`` / ``Max`` take RFC 3339 timestamps."""
    secs, rem = divmod(int(ns), 1_000_000_000)
    base = _dt.datetime.fromtimestamp(secs, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{rem:09d}Z"


def _otlp_value(value: Any) -> Any:
    """Unwrap an OTLP JSON ``AnyValue`` (``{"stringValue": ...}``)."""
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return value["intValue"]
    if "arrayValue" in value:
        return [_otlp_value(v) for v in (value["arrayValue"] or {}).get("values") or []]
    if "kvlistValue" in value:
        return {
            kv.get("key"): _otlp_value(kv.get("value"))
            for kv in (value["kvlistValue"] or {}).get("values") or []
        }
    return value


def _v3_traces_to_v1(data: Any) -> List[Dict[str, Any]]:
    """Regroup a v3 answer (``result.resourceSpans``, OTLP JSON, hex ids) into
    the classic ``data[]`` trace shape so one search path serves both APIs:
    ``spans[].tags`` from the attributes plus ``otel.status_code`` /
    ``otel.status_description`` / ``error`` from the status, ``CHILD_OF``
    references from ``parentSpanId``, one process per resource."""
    result = data.get("result") if isinstance(data, dict) else None
    resource_spans = (result or {}).get("resourceSpans") if isinstance(result, dict) else None
    traces: Dict[str, Dict[str, Any]] = {}
    for idx, rs in enumerate(resource_spans or []):
        if not isinstance(rs, dict):
            continue
        res_attrs = {
            a.get("key"): _otlp_value(a.get("value"))
            for a in ((rs.get("resource") or {}).get("attributes") or [])
            if isinstance(a, dict)
        }
        pid = f"p{idx}"
        process = {"serviceName": str(res_attrs.get("service.name") or ""), "tags": []}
        for scope in rs.get("scopeSpans") or []:
            for sp in (scope or {}).get("spans") or []:
                if not isinstance(sp, dict):
                    continue
                tid = str(sp.get("traceId") or "")
                if not tid:
                    continue
                tags = [
                    {"key": a.get("key"), "type": "string", "value": _otlp_value(a.get("value"))}
                    for a in sp.get("attributes") or []
                    if isinstance(a, dict) and a.get("key")
                ]
                status = sp.get("status") or {}
                code = status.get("code")
                if code == 2 or code == "STATUS_CODE_ERROR":
                    tags.append({"key": "otel.status_code", "type": "string", "value": "ERROR"})
                    tags.append({"key": "error", "type": "bool", "value": True})
                elif code == 1 or code == "STATUS_CODE_OK":
                    tags.append({"key": "otel.status_code", "type": "string", "value": "OK"})
                if status.get("message"):
                    tags.append(
                        {
                            "key": "otel.status_description",
                            "type": "string",
                            "value": str(status.get("message")),
                        }
                    )
                start_ns = _int(sp.get("startTimeUnixNano"))
                end_ns = _int(sp.get("endTimeUnixNano"))
                parent = sp.get("parentSpanId") or None
                v1_span = {
                    "traceID": tid,
                    "spanID": sp.get("spanId"),
                    "operationName": sp.get("name") or "",
                    "startTime": start_ns // 1000,
                    "duration": max(0, end_ns - start_ns) // 1000,
                    "references": (
                        [{"refType": "CHILD_OF", "traceID": tid, "spanID": parent}]
                        if parent
                        else []
                    ),
                    "tags": tags,
                    "processID": pid,
                }
                trace = traces.setdefault(tid, {"traceID": tid, "spans": [], "processes": {}})
                trace["spans"].append(v1_span)
                trace["processes"][pid] = process
    return list(traces.values())


@register
class JaegerAdapter(BackendAdapter):
    handles = frozenset({"jaeger"})
    query_lang_label = "Jaeger tags (key=value)"
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
        endpoint = cfg.get("endpoint") or ""
        parsed = _urlparse.urlparse(endpoint)
        host = parsed.hostname or "localhost"
        scheme = parsed.scheme or "http"
        port = cfg.get("query_port") or parsed.port or _DEFAULT_JAEGER_QUERY_PORT
        self.query_url = f"{scheme}://{rewrite_host_for_docker(host)}:{port}"
        # Optional bearer for cloud-hosted Jaeger / authenticated proxy.
        self.api_key = resolve_env_or_literal(cfg, "api_key", "api_key_env")
        self.default_service = default_service_name(cfg)
        # ``query_api``: ``v1`` (classic ``/api/traces``), ``v3`` (Jaeger v2's
        # ``/api/v3``), or ``auto`` (probed once per query URL).
        api = str(cfg.get("query_api") or "auto").strip().lower()
        self.query_api = api if api in ("v1", "v3") else "auto"

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        base["default_service"] = self.default_service
        base["query_api"] = (
            self.query_api
            if self.query_api != "auto"
            else _API_PROBE_CACHE.get(self.query_url, "auto")
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
        cached = _API_PROBE_CACHE.get(self.query_url)
        if cached:
            return cached
        api = "v1"
        try:
            data = http_get_json(
                f"{self.query_url}/api/services", headers=self._headers(), timeout=10.0
            )
            classic = isinstance(data, dict) and "data" in data
        except BackendError as exc:
            if exc.kind != "not_found":
                raise
            classic = False
        if not classic:
            try:
                data = http_get_json(
                    f"{self.query_url}/api/v3/services", headers=self._headers(), timeout=10.0
                )
                if isinstance(data, dict) and "services" in data:
                    api = "v3"
            except BackendError as exc:
                if exc.kind != "not_found":
                    raise
        _API_PROBE_CACHE[self.query_url] = api
        return api

    def _build_query_v3(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> List[Tuple[str, str]]:
        """The v3 grammar (verified on Jaeger 2.21.0): camelCase keys,
        RFC 3339 bounds, ``searchDepth`` for the limit, ``durationMin`` as
        ``<n>ms`` and the tag map JSON-encoded in ``query.attributes``
        (``attributes[k]=v`` and ``attributes.k=v`` are silently ignored)."""
        end_ns = int(end_s) * 1_000_000_000
        if f.before_ns:
            end_ns = min(end_ns, int(f.before_ns))
        params: List[Tuple[str, str]] = [
            ("query.serviceName", f.service or self.default_service),
            ("query.startTimeMin", _rfc3339(int(start_s) * 1_000_000_000)),
            ("query.startTimeMax", _rfc3339(end_ns)),
            ("query.searchDepth", str(int(limit) + 1)),
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
        hdr: Dict[str, str] = {}
        if self.api_key:
            hdr["Authorization"] = f"Bearer {self.api_key}"
        return hdr

    # ── Filter translation ───────────────────────────────────────────

    def _parse_raw_tags(self, raw: Optional[str]) -> Dict[str, str]:
        return parse_kv_tokens(raw)

    def _tags(self, f: StructuredFilter) -> Dict[str, Any]:
        tags: Dict[str, Any] = dict(f.attr_equals)
        tags.update(self._parse_raw_tags(f.raw))
        if f.status == "error":
            tags["error"] = "true"
        return tags

    def _build_query(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> Dict[str, Any]:
        end_us = int(end_s) * 1_000_000
        if f.before_ns:
            # The cursor is the query's end bound (µs); strictly_older_traces()
            # makes the cut exact afterwards.
            end_us = min(end_us, int(f.before_ns) // 1000)
        params: Dict[str, Any] = {
            "service": f.service or self.default_service,
            # One beyond the page so has_more is exact.
            "limit": int(limit) + 1,
            "start": int(start_s) * 1_000_000,
            "end": end_us,
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

    def _find_root_span(self, spans: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        ids = {sp.get("spanID") for sp in spans}
        for sp in spans:
            refs = sp.get("references") or []
            child_of = [r for r in refs if r.get("refType") == "CHILD_OF"]
            if not child_of or not any(r.get("spanID") in ids for r in child_of):
                return sp
        return spans[0] if spans else None

    def _root_matches(
        self, root: Dict[str, Any], attrs: Dict[str, Any], f: StructuredFilter
    ) -> bool:
        """Whether the ROOT span itself satisfies the filter. Jaeger's ``tags=``
        and ``operation=`` match any span of a trace; roots-only means the root
        must match, so the same predicates are re-checked here (#295)."""
        refs = root.get("references") or []
        if refs and any(r.get("refType") == "CHILD_OF" for r in refs):
            return False  # an orphan: its real root is outside the window
        name = root.get("operationName") or ""
        if f.name_prefix and not name.startswith(f.name_prefix):
            return False
        if f.name_regex and name != f.name_regex:
            return False
        for k, v in self._tags(f).items():
            have = attrs.get(k)
            if have is None:
                return False
            if str(have).lower() != str(v).lower():
                return False
        if f.min_duration_ms and _int(root.get("duration")) < int(f.min_duration_ms) * 1000:
            return False
        return True

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
        if self._api() == "v3":
            v3_params = self._build_query_v3(f, start_s, end_s, limit)
            url = f"{self.query_url}/api/v3/traces?{_urlparse.urlencode(v3_params)}"
            try:
                data = http_get_json(url, headers=self._headers(), timeout=15.0)
            except BackendError as exc:
                # Jaeger v2 answers an empty search with 404 "No traces found".
                if exc.kind != "not_found":
                    raise
                data = {}
            items: List[Any] = _v3_traces_to_v1(data)
        else:
            params = self._build_query(f, start_s, end_s, limit)
            url = f"{self.query_url}/api/traces?{_urlparse.urlencode(params)}"
            data = http_get_json(url, headers=self._headers(), timeout=15.0)
            raw_items = data.get("data") if isinstance(data, dict) else None
            items = raw_items if isinstance(raw_items, list) else []

        traces: List[Dict[str, Any]] = []
        for t in items:
            if not isinstance(t, dict):
                continue
            trace_id = t.get("traceID")
            spans = t.get("spans") or []
            if not trace_id or not spans:
                continue
            root = self._find_root_span(spans)
            if root is None:
                continue
            attrs = self._span_tags_as_dict(root)
            if f.roots_only and not self._root_matches(root, attrs, f):
                continue
            card = root
            if not f.roots_only and f.name_prefix:
                # Widened to every span (the kind filter): the card is the
                # first span carrying the prefix, as the other backends show
                # it, with that span's own attributes, start and duration.
                card = next(
                    (
                        sp
                        for sp in sorted(spans, key=lambda sp: _int(sp.get("startTime")))
                        if str(sp.get("operationName") or "").startswith(f.name_prefix)
                    ),
                    None,
                )
                if card is None:
                    continue
                attrs = self._span_tags_as_dict(card)
            processes = t.get("processes") or {}
            service = self._service_name_for_span(card, processes)
            # Keep only the card-relevant keys for the list payload; the
            # detail view will expose the rest.
            keep = {k: v for k, v in attrs.items() if k in _CARD_TAGS}
            keep["name"] = card.get("operationName") or ""
            if self._status_of(attrs).get("code") == 2:
                keep["status"] = "error"

            start_us = _int(card.get("startTime"))
            start_ns = start_us * 1000 if start_us else 0
            dur_us = _int(card.get("duration"))

            traces.append(
                {
                    "traceID": trace_id,
                    "spanCount": len(spans),
                    "rootServiceName": service,
                    "rootTraceName": card.get("operationName") or "",
                    "startTimeUnixNano": str(start_ns) if start_ns else "0",
                    "durationMs": dur_us // 1000 if dur_us else 0,
                    "spanSets": [
                        {
                            "spans": [
                                {
                                    "spanID": card.get("spanID"),
                                    "name": card.get("operationName") or "",
                                    "attributes": otlp_attrs_from_dict(keep),
                                }
                            ],
                            "matched": len(spans),
                        }
                    ],
                }
            )
        return trace_page(strictly_older_traces(traces, f), limit)

    def trace_url(self, trace_id: str) -> Optional[str]:
        return f"{self.query_url}/trace/{trace_id}"

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        if self._api() == "v3":
            # OTLP JSON with hex ids: the detail's own shape, passed through.
            url = f"{self.query_url}/api/v3/traces/{trace_id}"
            try:
                data = http_get_json(url, headers=self._headers(), timeout=20.0)
            except BackendError as exc:
                if exc.kind == "not_found":
                    raise BackendError(404, f"Trace {trace_id} not found in Jaeger", "not_found")
                raise
            result = data.get("result") if isinstance(data, dict) else None
            batches = (result or {}).get("resourceSpans") if isinstance(result, dict) else None
            batches = [b for b in (batches or []) if isinstance(b, dict)]
            n = sum(
                len((scope or {}).get("spans") or [])
                for b in batches
                for scope in b.get("scopeSpans") or []
            )
            if n == 0:
                raise BackendError(404, f"Trace {trace_id} not found in Jaeger", "not_found")
            return {"batches": batches, "span_count": n, "truncated": False}
        url = f"{self.query_url}/api/traces/{trace_id}"
        data = http_get_json(url, headers=self._headers(), timeout=20.0)
        items = data.get("data") if isinstance(data, dict) else None
        if not items:
            raise BackendError(404, f"Trace {trace_id} not found in Jaeger", "not_found")
        trace = items[0] if isinstance(items, list) else items
        processes = trace.get("processes") or {}
        spans = trace.get("spans") or []

        by_service: Dict[str, List[Dict[str, Any]]] = {}
        for sp in spans:
            if not isinstance(sp, dict):
                continue
            attrs = self._span_tags_as_dict(sp)
            service = self._service_name_for_span(sp, processes)
            refs = sp.get("references") or []
            parent = None
            for r in refs:
                if r.get("refType") == "CHILD_OF" and r.get("spanID"):
                    parent = r["spanID"]
                    break
            start_us = _int(sp.get("startTime"))
            dur_us = _int(sp.get("duration"))
            start_ns = start_us * 1000
            end_ns = (start_us + dur_us) * 1000

            otlp_span = {
                "traceId": trace_id,
                "spanId": sp.get("spanID"),
                "parentSpanId": parent or None,
                "name": sp.get("operationName") or "",
                "kind": 1,
                "startTimeUnixNano": str(start_ns) if start_ns else "0",
                "endTimeUnixNano": str(end_ns) if end_ns else "0",
                "attributes": otlp_attrs_from_dict(attrs),
                "status": self._status_of(attrs),
            }
            by_service.setdefault(service, []).append(otlp_span)

        batches = []
        for service, spans_list in by_service.items():
            resource_attrs = otlp_attrs_from_dict({"service.name": service} if service else {})
            batches.append(
                {
                    "resource": {"attributes": resource_attrs},
                    "scopeSpans": [{"spans": spans_list}],
                }
            )
        return {"batches": batches, "span_count": len(spans), "truncated": False}
