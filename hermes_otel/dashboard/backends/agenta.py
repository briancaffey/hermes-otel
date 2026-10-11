"""Agenta adapter — ``POST /api/spans/query`` with the project API key (#371).

Agenta keeps one row per span with its own derived namespace next to the
attributes the plugin sent: ``attributes.ag.{type,metrics,data,meta,session}``
plus ``gen_ai``, ``llm``, ``hermes``, ``openinference``, ``input``, ``output``,
``session`` as nested objects. The query API takes a ``TracingQuery``:
``windowing`` (``oldest`` / ``newest`` ISO timestamps, ``limit``, ``order``),
``filtering`` (``and`` / ``or`` trees of ``{field, key, value, operator}``)
and ``formatting``. Verified on v0.122.4 (2026-10-11): ``parent_id is null``
selects roots, ``span_name`` takes ``is`` / ``startswith``, ``status_code``
takes ``is`` / ``is_not`` (``STATUS_CODE_ERROR``), ``trace_id`` takes ``is``,
and ``attributes`` with a dotted ``key`` takes ``eq`` / ``gt`` / ``contains``,
so every search-bar field is a server predicate except the duration (no
field). Ingest is asynchronous (a Redis stream), so a span appears a few
seconds after export.

The read side uses the same ``Authorization: ApiKey`` the exporter uses; the
key scopes everything to one project, which is why ``service`` is not a
filter. Resource attributes are not stored, so the detail's resource is the
plugin's default service name.
"""

from __future__ import annotations

import copy
import datetime as _dt
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from . import default_service_name, register
from .base import (
    BackendAdapter,
    BackendError,
    ConfigError,
    StructuredFilter,
    http_post_json,
    otlp_attrs_from_dict,
    parse_kv_tokens,
    resolve_env_or_literal,
    rewrite_host_for_docker,
    strictly_older_traces,
    trace_page,
)
from .victoria import rfc3339_to_ns

_CLOUD = {"us": "https://us.cloud.agenta.ai", "eu": "https://eu.cloud.agenta.ai"}
# A widened search returns spans, several per trace; fetch a few per requested row.
_ROWS_PER_TRACE = 4
_MAX_TRACE_SPANS = 2000

_CARD_KEYS = frozenset(
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
# Agenta's derived copies of the inputs and outputs; the plugin's own
# ``input.value`` / ``gen_ai.input.messages`` already carry them.
_FREE_TEXT_KEYS = ("input.value", "output.value")  # the roots' prompt and answer
_DROP_PREFIXES = ("ag.data.", "ag.meta.")
_KIND = {
    "SPAN_KIND_INTERNAL": 1,
    "SPAN_KIND_SERVER": 2,
    "SPAN_KIND_CLIENT": 3,
    "SPAN_KIND_PRODUCER": 4,
    "SPAN_KIND_CONSUMER": 5,
}
_STATUS = {"STATUS_CODE_OK": 1, "STATUS_CODE_ERROR": 2}


def flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    """Nested attribute objects → dotted keys (``gen_ai.usage.total_tokens``)."""
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and v:
                out.update(flatten(v, key))
            else:
                out[key] = v
    return out


def iso(ns: int) -> str:
    secs, rem = divmod(int(ns), 1_000_000_000)
    base = _dt.datetime.fromtimestamp(secs, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{rem // 1000:06d}Z"


def _cond(
    field: str, operator: str, value: Any = None, key: Optional[str] = None
) -> Dict[str, Any]:
    c: Dict[str, Any] = {"field": field, "operator": operator}
    if key is not None:
        c["key"] = key
    if value is not None or operator in ("is", "is_not"):
        c["value"] = value
    return c


@register
class AgentaAdapter(BackendAdapter):
    handles = frozenset({"agenta"})
    query_lang_label = "Agenta attribute filter (key=value)"
    raw_placeholder = "tool.name=terminal ag.type.span=tool"
    supports_metrics = False
    supports_logs = False
    filter_support = {
        "service": "none",  # the key is the project
        "name": "server",
        "model": "server",
        "session": "server",
        "tool": "server",
        "min_duration": "client",  # no duration field in the grammar
        "status_error": "server",
        "status_ok": "server",
        "free_text": "server",
        "raw": "server",
        "roots_only": "server",
    }

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        region = str(cfg.get("region") or "us").strip().lower()
        endpoint = str(cfg.get("endpoint") or _CLOUD.get(region, _CLOUD["us"]))
        parsed = _urlparse.urlparse(endpoint)
        host = rewrite_host_for_docker(parsed.hostname or "localhost")
        port = f":{parsed.port}" if parsed.port else ""
        self.query_url = f"{parsed.scheme or 'http'}://{host}{port}"
        self.api_url = f"{self.query_url}/api"
        self.api_key = resolve_env_or_literal(cfg, "api_key", "api_key_env")
        if not self.api_key:
            import os

            self.api_key = (
                os.environ.get("OTEL_AGENTA_API_KEY", "").strip()
                or os.environ.get("AGENTA_API_KEY", "").strip()
                or None
            )
        self.default_service = default_service_name(cfg)
        self.ui_url = (cfg.get("ui_url") or "").strip().rstrip("/") or None

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.api_url
        base["default_service"] = self.default_service
        return base

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise ConfigError("Agenta needs api_key / api_key_env (the project API key) to query")
        return {"Authorization": f"ApiKey {self.api_key}"}

    def _query(self, body: Dict[str, Any], timeout: float = 20.0) -> List[Dict[str, Any]]:
        data = http_post_json(
            f"{self.api_url}/spans/query", body, headers=self._headers(), timeout=timeout
        )
        spans = data.get("spans") if isinstance(data, dict) else None
        return [s for s in (spans or []) if isinstance(s, dict)]

    # ── filter translation ───────────────────────────────────────────

    def _conditions(self, f: StructuredFilter) -> List[Dict[str, Any]]:
        conds: List[Dict[str, Any]] = []
        if f.roots_only:
            conds.append(_cond("parent_id", "is", None))
        if f.name_regex:
            conds.append(_cond("span_name", "is", f.name_regex))
        if f.name_prefix:
            conds.append(_cond("span_name", "startswith", f.name_prefix))
        for k, v in {**f.attr_equals, **parse_kv_tokens(f.raw)}.items():
            conds.append(_cond("attributes", "eq", v, key=k))
        if f.status == "error":
            conds.append(_cond("status_code", "is", "STATUS_CODE_ERROR"))
        elif f.status == "ok":
            conds.append(_cond("status_code", "is_not", "STATUS_CODE_ERROR"))
        return conds

    def _bodies(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        """One query per free-text target. A nested ``{"operator": "or",
        "conditions": [...]}`` group is accepted by the API but parsed as an
        empty leaf condition and dropped (v0.122: a free-text search returned
        every root), so "input OR output contains" is two flat ``and`` queries
        merged client-side."""
        base = self._body(f, start_s, end_s, limit)
        if not f.free_text:
            return [base]
        bodies = []
        for key in _FREE_TEXT_KEYS:
            body = copy.deepcopy(base)
            conds = list(body.get("filtering", {}).get("conditions", []))
            conds.append(_cond("attributes", "contains", f.free_text, key=key))
            body["filtering"] = {"operator": "and", "conditions": conds}
            bodies.append(body)
        return bodies

    def _body(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        end_ns = int(end_s) * 1_000_000_000
        if f.before_ns:
            end_ns = min(end_ns, int(f.before_ns))
        body: Dict[str, Any] = {
            "formatting": {"focus": "span", "format": "agenta"},
            "windowing": {
                "oldest": iso(int(start_s) * 1_000_000_000),
                "newest": iso(end_ns),
                "limit": int(limit),
                "order": "descending",
            },
        }
        conds = self._conditions(f)
        if conds:
            body["filtering"] = {"operator": "and", "conditions": conds}
        return body

    # ── shapes ───────────────────────────────────────────────────────

    @staticmethod
    def _attrs(span: Dict[str, Any]) -> Dict[str, Any]:
        flat = flatten(span.get("attributes") or {})
        return {k: v for k, v in flat.items() if not k.startswith(_DROP_PREFIXES)}

    @staticmethod
    def _times(span: Dict[str, Any]) -> tuple:
        start = rfc3339_to_ns(span.get("start_time"))
        end = rfc3339_to_ns(span.get("end_time")) or start
        return start, end

    def _counts(self, trace_ids: List[str]) -> Dict[str, int]:
        if not trace_ids:
            return {}
        spans = self._query(
            {
                "formatting": {"focus": "span", "format": "agenta"},
                "windowing": {"limit": _MAX_TRACE_SPANS},
                "filtering": {
                    "operator": "and",
                    "conditions": [_cond("trace_id", "in", trace_ids)],
                },
            }
        )
        counts: Dict[str, int] = {}
        for sp in spans:
            tid = str(sp.get("trace_id") or "")
            counts[tid] = counts.get(tid, 0) + 1
        return counts

    # ── public API ───────────────────────────────────────────────────

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        fetch = int(limit) + 1 if f.roots_only else (int(limit) + 1) * _ROWS_PER_TRACE
        seen: set = set()
        spans: List[Dict[str, Any]] = []
        for body in self._bodies(f, start_s, end_s, fetch):
            for sp in self._query(body):
                key = (sp.get("trace_id"), sp.get("span_id"))
                if key in seen:
                    continue
                seen.add(key)
                spans.append(sp)
        spans.sort(key=lambda sp: self._times(sp)[0], reverse=True)
        spans = spans[:fetch]
        cards: Dict[str, Dict[str, Any]] = {}
        for sp in spans:
            tid = str(sp.get("trace_id") or "")
            if not tid:
                continue
            start, end = self._times(sp)
            if f.min_duration_ms and (end - start) < int(f.min_duration_ms) * 1_000_000:
                continue
            prev = cards.get(tid)
            if prev is None or start < self._times(prev)[0]:
                cards[tid] = sp
        counts = self._counts(list(cards))
        rows: List[Dict[str, Any]] = []
        for tid, sp in cards.items():
            attrs = self._attrs(sp)
            keep = {k: v for k, v in attrs.items() if k in _CARD_KEYS}
            keep["name"] = sp.get("span_name") or ""
            if sp.get("status_code") == "STATUS_CODE_ERROR":
                keep["status"] = "error"
            start, end = self._times(sp)
            n = counts.get(tid, 1)
            rows.append(
                {
                    "traceID": tid,
                    "spanCount": n,
                    "rootServiceName": self.default_service,
                    "rootTraceName": sp.get("span_name") or "",
                    "startTimeUnixNano": str(start) if start else "0",
                    "durationMs": max(0, end - start) // 1_000_000,
                    "spanSets": [
                        {
                            "spans": [
                                {
                                    "spanID": sp.get("span_id"),
                                    "name": sp.get("span_name") or "",
                                    "attributes": otlp_attrs_from_dict(keep),
                                }
                            ],
                            "matched": n,
                        }
                    ],
                }
            )
        page = trace_page(strictly_older_traces(rows, f), limit)
        if not f.roots_only and len(spans) >= fetch:
            page["has_more"] = True
        return page

    def trace_url(self, trace_id: str) -> Optional[str]:
        # The observability page lives under /w/<workspace>/p/<project>/…, ids
        # the entry does not know; ``ui_url`` may name that page.
        return self.ui_url

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        spans = self._query(
            {
                "formatting": {"focus": "span", "format": "agenta"},
                "windowing": {"limit": _MAX_TRACE_SPANS, "order": "ascending"},
                "filtering": {"operator": "and", "conditions": [_cond("trace_id", "is", trace_id)]},
            }
        )
        if not spans:
            raise BackendError(404, f"Trace {trace_id} not found in Agenta", "not_found")
        otlp = []
        for sp in spans:
            start, end = self._times(sp)
            otlp.append(
                {
                    "traceId": sp.get("trace_id") or trace_id,
                    "spanId": sp.get("span_id"),
                    "parentSpanId": sp.get("parent_id") or None,
                    "name": sp.get("span_name") or "",
                    "kind": _KIND.get(str(sp.get("span_kind") or ""), 1),
                    "startTimeUnixNano": str(start) if start else "0",
                    "endTimeUnixNano": str(end) if end else "0",
                    "attributes": otlp_attrs_from_dict(self._attrs(sp)),
                    "status": {
                        "code": _STATUS.get(str(sp.get("status_code") or ""), 0),
                        "message": "",
                    },
                }
            )
        batch = {
            "resource": {
                "attributes": otlp_attrs_from_dict({"service.name": self.default_service})
            },
            "scopeSpans": [{"spans": otlp}],
        }
        return {
            "batches": [batch],
            "span_count": len(otlp),
            "truncated": len(spans) >= _MAX_TRACE_SPANS,
        }
