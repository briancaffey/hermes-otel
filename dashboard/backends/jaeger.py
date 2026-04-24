"""Jaeger adapter — classic HTTP query API at ``/api/traces``.

Self-hosted Jaeger usually runs unauthenticated on localhost. Cloud
offerings (Grafana Cloud Traces, etc.) put an API gateway in front;
the adapter supports a bearer token via ``api_key`` / ``api_key_env``
when present.

Jaeger search filters by ``service`` + ``tags``; there is no TraceQL
equivalent. Structured filters map directly, and the raw query field
accepts additional ``key=value`` pairs that become more tags.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from . import register
from .base import (
    BackendAdapter,
    StructuredFilter,
    http_get_json,
    otlp_attrs_from_dict,
    otlp_status,
    resolve_env_or_literal,
    rewrite_host_for_docker,
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


@register
class JaegerAdapter(BackendAdapter):
    handles = frozenset({"jaeger"})
    query_lang_label = "Jaeger tags (key=value)"
    raw_placeholder = "http.status_code=500 error=true"

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
        # Default service name filter — if the user ships a single-
        # service Hermes install, defaulting here saves the structured
        # filter from being required.
        self.default_service = cfg.get("service_name") or "hermes-agent"

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        base["default_service"] = self.default_service
        return base

    def _headers(self) -> Dict[str, str]:
        hdr: Dict[str, str] = {}
        if self.api_key:
            hdr["Authorization"] = f"Bearer {self.api_key}"
        return hdr

    # ── Filter translation ───────────────────────────────────────────

    def _parse_raw_tags(self, raw: Optional[str]) -> Dict[str, str]:
        if not raw:
            return {}
        tags: Dict[str, str] = {}
        for tok in raw.split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                if k.strip() and v.strip():
                    tags[k.strip()] = v.strip().strip("\"'")
        return tags

    def _build_query(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "service": f.service or self.default_service,
            "limit": int(limit),
            "start": int(start_s) * 1_000_000,
            "end": int(end_s) * 1_000_000,
        }
        if f.name_regex:
            params["operation"] = f.name_regex
        if f.min_duration_ms and f.min_duration_ms > 0:
            params["minDuration"] = f"{int(f.min_duration_ms)}ms"

        tags: Dict[str, Any] = dict(f.attr_equals)
        tags.update(self._parse_raw_tags(f.raw))
        if f.status == "error":
            tags["error"] = "true"
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

    def _service_name_for_span(
        self, span: Dict[str, Any], processes: Dict[str, Any]
    ) -> str:
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

    # ── Public API ───────────────────────────────────────────────────

    def search(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> Dict[str, Any]:
        params = self._build_query(f, start_s, end_s, limit)
        url = f"{self.query_url}/api/traces?{_urlparse.urlencode(params)}"
        data = http_get_json(url, headers=self._headers(), timeout=15.0)

        items = data.get("data") if isinstance(data, dict) else None
        items = items if isinstance(items, list) else []

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
            if f.roots_only:
                # Jaeger has no server-side root-only filter; enforce
                # client-side by skipping traces where the root span
                # was not itself a match for the user's filter. When
                # the root has no parent-references we count it as a
                # match; otherwise drop the trace.
                refs = root.get("references") or []
                if refs and any(r.get("refType") == "CHILD_OF" for r in refs):
                    continue
            processes = t.get("processes") or {}
            service = self._service_name_for_span(root, processes)
            attrs = self._span_tags_as_dict(root)
            # Keep only the card-relevant keys for the list payload; the
            # detail view will expose the rest.
            keep = {k: v for k, v in attrs.items() if k in _CARD_TAGS}
            keep["name"] = root.get("operationName") or ""
            if attrs.get("error"):
                keep["status"] = "error"

            start_us = int(root.get("startTime") or 0)
            start_ns = start_us * 1000 if start_us else 0
            dur_us = int(root.get("duration") or 0)

            traces.append(
                {
                    "traceID": trace_id,
                    "rootServiceName": service,
                    "rootTraceName": root.get("operationName") or "",
                    "startTimeUnixNano": str(start_ns) if start_ns else "0",
                    "durationMs": dur_us // 1000 if dur_us else 0,
                    "spanSets": [
                        {
                            "spans": [
                                {
                                    "spanID": root.get("spanID"),
                                    "name": root.get("operationName") or "",
                                    "attributes": otlp_attrs_from_dict(keep),
                                }
                            ],
                            "matched": len(spans),
                        }
                    ],
                }
            )
        traces.sort(
            key=lambda t: int(t.get("startTimeUnixNano") or 0),
            reverse=True,
        )
        return {"traces": traces}

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        url = f"{self.query_url}/api/traces/{trace_id}"
        data = http_get_json(url, headers=self._headers(), timeout=20.0)
        items = data.get("data") if isinstance(data, dict) else None
        if not items:
            return {"batches": []}
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
            start_us = int(sp.get("startTime") or 0)
            dur_us = int(sp.get("duration") or 0)
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
                "status": otlp_status("error" if attrs.get("error") else "ok"),
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
        return {"batches": batches}
