"""Uptrace adapter — REST at ``/api/v1/tracing/{project_id}/…``.

Auth is bearer-token. The token is the secret portion of the DSN
(``http://<secret>@host:port``). Uptrace defaults to project 1 for
single-tenant installs; override with ``project_id`` in the backend
cfg when you have more than one project.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from fastapi import HTTPException

from . import register
from .base import (
    BackendAdapter,
    StructuredFilter,
    http_get_json,
    http_post_json,
    ns_from_any,
    otlp_attrs_from_dict,
    otlp_status,
    resolve_env_or_literal,
    rewrite_host_for_docker,
)

_DEFAULT_UPTRACE_HTTP_PORT = 14318


def _extract_dsn(dsn: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return ``(token, host_base)`` from an Uptrace DSN.

    Accepts ``http://<secret>@host:port[?grpc=...]`` — only the HTTP
    base is used; gRPC is ignored because the adapter speaks REST.
    """
    if not dsn:
        return None, None
    try:
        parsed = _urlparse.urlparse(dsn)
    except Exception:
        return None, None
    token = parsed.username or None
    host = parsed.hostname
    scheme = parsed.scheme or "http"
    port = parsed.port or _DEFAULT_UPTRACE_HTTP_PORT
    base = f"{scheme}://{host}:{port}" if host else None
    return token, base


_TAG_KEYS = (
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
)


@register
class UptraceAdapter(BackendAdapter):
    handles = frozenset({"uptrace"})
    query_lang_label = "UQL"
    raw_placeholder = 'span.name like "api.%" | where span.duration > 1s'

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        token, base = _extract_dsn(resolve_env_or_literal(cfg, "dsn", "dsn_env"))

        # Endpoint may override the DSN host (useful when UI uses a
        # different port than OTLP ingest).
        endpoint = cfg.get("endpoint") or ""
        if endpoint:
            parsed = _urlparse.urlparse(endpoint)
            host = parsed.hostname or "localhost"
            scheme = parsed.scheme or "http"
            port = cfg.get("query_port") or _DEFAULT_UPTRACE_HTTP_PORT
            base = f"{scheme}://{host}:{port}"

        if base:
            parsed = _urlparse.urlparse(base)
            base = f"{parsed.scheme}://{rewrite_host_for_docker(parsed.hostname or 'localhost')}:{parsed.port or _DEFAULT_UPTRACE_HTTP_PORT}"

        self.query_url = base or ""
        self.token = token or resolve_env_or_literal(cfg, "api_key", "api_key_env")
        self.project_id = int(cfg.get("project_id") or 1)

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        base["project_id"] = self.project_id
        base["auth_required"] = self.token is None
        if self.token is None:
            base["auth_hint"] = (
                "Uptrace requires a bearer token from your DSN's secret. "
                "Set ``dsn`` or ``dsn_env`` on the uptrace backend entry."
            )
        return base

    def _headers(self) -> Dict[str, str]:
        if not self.token:
            raise HTTPException(
                status_code=502,
                detail="Uptrace requires a DSN token. Set dsn/dsn_env.",
            )
        return {"Authorization": f"Bearer {self.token}"}

    def _build_uql(self, f: StructuredFilter) -> str:
        user_raw = (f.raw or "").strip()
        parts: List[str] = []
        if f.service:
            parts.append(f'where span.service_name = "{_esc(f.service)}"')
        if f.name_regex:
            parts.append(f'where span.name like "{_esc(f.name_regex)}"')
        if f.status == "error":
            parts.append('where span.status_code = "error"')
        elif f.status == "ok":
            parts.append('where span.status_code = "ok"')
        if f.min_duration_ms:
            parts.append(f"where span.duration >= {int(f.min_duration_ms)}ms")
        if f.roots_only:
            parts.append('where span.parent_id = ""')
        for k, v in f.attr_equals.items():
            parts.append(f'where span.{k} = "{_esc(str(v))}"')
        # Newest first.
        parts.append("order by span.time desc")

        # Default projection — Uptrace UI queries look like:
        # ``group by span.name | span.count | span.duration.p50``
        # For list-style retrieval we select specific columns.
        select = (
            "span.trace_id span.id span.name span.service_name "
            "span.time span.duration span.status_code"
        )

        pipeline: List[str] = []
        if user_raw:
            pipeline.append(user_raw)
        else:
            pipeline.append(select)
        pipeline.extend(parts)
        return " | ".join(pipeline)

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        # Uptrace's documented span-search endpoint:
        # POST /api/v1/tracing/{project_id}/search/spans
        body = {
            "query": self._build_uql(f),
            "time_gte": start_s * 1000,
            "time_lt": end_s * 1000,
            "limit": int(limit),
        }
        url = f"{self.query_url}/api/v1/tracing/{self.project_id}/search/spans"
        data = http_post_json(url, body, headers=self._headers(), timeout=15.0)

        spans = _extract_spans(data)
        traces: Dict[str, Dict[str, Any]] = {}
        for sp in spans:
            trace_id = sp.get("traceId") or sp.get("trace_id") or sp.get("span.trace_id")
            if not trace_id:
                continue
            parent_id = sp.get("parentId") or sp.get("parent_id") or sp.get("span.parent_id")
            # Keep the first root-ish span seen per trace as the list
            # entry. "Root-ish" = no parent id; fall back to first seen.
            existing = traces.get(trace_id)
            if existing and parent_id:
                continue

            start_ns = ns_from_any(sp.get("time") or sp.get("startTime") or sp.get("span.time"))
            dur_raw = sp.get("duration") or sp.get("span.duration")
            duration_ms = _duration_to_ms(dur_raw)
            attrs_map: Dict[str, Any] = {}
            raw_attrs = sp.get("attributes") or sp.get("attrs") or {}
            if isinstance(raw_attrs, dict):
                for k in _TAG_KEYS:
                    if k in raw_attrs:
                        attrs_map[k] = raw_attrs[k]
            name = sp.get("name") or sp.get("span.name") or ""
            service = (
                sp.get("serviceName") or sp.get("service_name") or sp.get("span.service_name") or ""
            )
            status = sp.get("statusCode") or sp.get("span.status_code")
            if status:
                attrs_map["status"] = status

            traces[trace_id] = {
                "traceID": trace_id,
                "rootServiceName": service,
                "rootTraceName": name,
                "startTimeUnixNano": str(start_ns) if start_ns else "0",
                "durationMs": duration_ms,
                "spanSets": [
                    {
                        "spans": [
                            {
                                "spanID": sp.get("id") or sp.get("span.id"),
                                "name": name,
                                "attributes": otlp_attrs_from_dict(attrs_map),
                            }
                        ],
                        "matched": 1,
                    }
                ],
            }

        out = list(traces.values())
        out.sort(
            key=lambda t: int(t.get("startTimeUnixNano") or 0),
            reverse=True,
        )
        return {"traces": out}

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        url = f"{self.query_url}/api/v1/tracing/{self.project_id}/traces/{trace_id}"
        data = http_get_json(url, headers=self._headers(), timeout=20.0)
        return _uptrace_trace_to_otlp(data)


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _duration_to_ms(raw: Any) -> int:
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        # Uptrace returns microseconds in some payloads, ns in others.
        n = int(raw)
        if n > 10**12:
            return n // 1_000_000
        if n > 10**9:
            return n // 1000
        return n
    return 0


def _extract_spans(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    for key in ("spans", "data", "rows", "items"):
        val = data.get(key)
        if isinstance(val, list):
            return [v for v in val if isinstance(v, dict)]
    return []


def _uptrace_trace_to_otlp(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {"batches": []}
    spans_raw = data.get("spans") or ((data.get("trace") or {}).get("spans")) or []
    if not isinstance(spans_raw, list):
        return {"batches": []}

    by_service: Dict[str, List[Dict[str, Any]]] = {}
    for sp in spans_raw:
        if not isinstance(sp, dict):
            continue
        attrs = sp.get("attributes") or sp.get("attrs") or {}
        if not isinstance(attrs, dict):
            attrs = {}
        start_ns = ns_from_any(sp.get("time") or sp.get("startTime"))
        duration_raw = sp.get("duration") or 0
        duration_ns = _duration_to_ns(duration_raw)
        end_ns = (start_ns + duration_ns) if start_ns else 0
        service = sp.get("serviceName") or sp.get("service_name") or ""

        otlp_span = {
            "traceId": sp.get("traceId") or sp.get("trace_id"),
            "spanId": sp.get("id") or sp.get("spanId") or sp.get("span_id"),
            "parentSpanId": sp.get("parentId") or sp.get("parent_id") or None,
            "name": sp.get("name") or "",
            "kind": int(sp.get("kind") or 1),
            "startTimeUnixNano": str(start_ns) if start_ns else "0",
            "endTimeUnixNano": str(end_ns) if end_ns else "0",
            "attributes": otlp_attrs_from_dict(attrs),
            "status": otlp_status(sp.get("statusCode") or sp.get("status_code")),
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


def _duration_to_ns(raw: Any) -> int:
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        n = int(raw)
        if n > 10**12:
            return n * 1000  # µs → ns
        if n > 10**9:
            return n  # already ns
        return n * 1_000_000  # ms → ns
    return 0
