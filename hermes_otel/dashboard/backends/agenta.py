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

from . import default_service_name, register
from .base import (
    BackendAdapter,
    StructuredFilter,
    http_post_json,
    not_found,
    parse_kv_tokens,
    query_origin,
    require_credential,
    resolve_env_or_literal,
)
from .paging import (
    mark_full_fetch,
    strictly_older_traces,
    trace_end_bound,
    trace_page,
    widened_fetch,
)
from .shapes import card_attrs, card_row, flatten_dotted, otlp_batches, otlp_span
from .victoria import rfc3339_to_ns

_CLOUD = {"us": "https://us.cloud.agenta.ai", "eu": "https://eu.cloud.agenta.ai"}
# A widened search returns spans, several per trace; fetch a few per requested row.
_ROWS_PER_TRACE = 4
_MAX_TRACE_SPANS = 2000

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
        # The cloud hosts carry no port; a self-hosted entry's endpoint does.
        origin = query_origin({**cfg, "endpoint": endpoint}, None, endpoint_port="first")
        self.api_url = f"{origin}/api"
        self.query_url = self.api_url
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
        base["default_service"] = self.default_service
        return base

    def _headers(self) -> Dict[str, str]:
        key = require_credential(
            self.api_key, "Agenta needs api_key / api_key_env (the project API key) to query"
        )
        return {"Authorization": f"ApiKey {key}"}  # Agenta's own scheme, not a bearer

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
        body: Dict[str, Any] = {
            "formatting": {"focus": "span", "format": "agenta"},
            "windowing": {
                "oldest": iso(int(start_s) * 1_000_000_000),
                "newest": iso(trace_end_bound(f, end_s, "ns")),
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
        # An empty nested object stays (``{}``): Agenta writes one for a
        # namespace it knows but the span did not fill.
        flat = flatten_dotted(span.get("attributes") or {}, keep_empty=True)
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
        fetch = widened_fetch(limit, f.roots_only, _ROWS_PER_TRACE, inclusive=True)
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
            start, end = self._times(sp)
            name = sp.get("span_name") or ""
            rows.append(
                card_row(
                    tid,
                    span_id=sp.get("span_id"),
                    name=name,
                    attrs=card_attrs(
                        self._attrs(sp), name, sp.get("status_code") == "STATUS_CODE_ERROR"
                    ),
                    start_ns=start,
                    duration_ms=max(0, end - start) // 1_000_000,
                    service=self.default_service,
                    span_count=counts.get(tid, 1),
                )
            )
        page = trace_page(strictly_older_traces(rows, f), limit)
        return mark_full_fetch(page, len(spans), fetch, widened=not f.roots_only)

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
            raise not_found(trace_id, "Agenta")
        otlp = []
        for sp in spans:
            start, end = self._times(sp)
            otlp.append(
                otlp_span(
                    sp.get("trace_id") or trace_id,
                    sp.get("span_id"),
                    sp.get("parent_id"),
                    sp.get("span_name"),
                    start_ns=start,
                    end_ns=end,
                    attrs=self._attrs(sp),
                    status={
                        "code": _STATUS.get(str(sp.get("status_code") or ""), 0),
                        "message": "",
                    },
                    kind=_KIND.get(str(sp.get("span_kind") or ""), 1),
                )
            )
        return {
            # Resource attributes are not stored: the plugin's service name stands in.
            "batches": otlp_batches({self.default_service: otlp}),
            "span_count": len(otlp),
            "truncated": len(spans) >= _MAX_TRACE_SPANS,
        }
