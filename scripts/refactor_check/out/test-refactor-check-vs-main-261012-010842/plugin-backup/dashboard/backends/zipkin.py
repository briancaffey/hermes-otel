"""Zipkin adapter — API v2 (``/api/v2/traces``, ``/api/v2/trace/{id}``) on
the query port, the same port the zipkin-otel module ingests OTLP on (#364).

Zipkin stores every attribute as a **string tag** (ints, bools and doubles
unquoted, arrays comma-joined, kvlists as JSON) and keeps the OTLP status as
the ``otel.status_code`` tag plus an ``error`` tag on ERROR. Span events are
annotations whose value is the event name, or ``"name":{...}`` when the event
carried attributes. The adapter turns those back into the OTLP shapes the
dashboard consumes; numeric-looking tag values are coerced except on id-like
keys so token counts render as numbers and session ids stay strings.

Search filters by ``serviceName`` + ``annotationQuery`` (``key=value and
key2=value2``), ``spanName`` (exact) and ``minDuration`` (µs); there is no
regex or negative search. ``annotationQuery`` matches ANY span of a trace, so
with ``roots_only`` the adapter re-checks the filter against the root span of
every returned trace, like the Jaeger adapter does (#295). The window is
``endTs`` (ms) plus ``lookback`` (ms).

Self-hosted Zipkin runs unauthenticated; a bearer token (``api_key`` /
``api_key_env``) is sent when present for an authenticating proxy.
"""

from __future__ import annotations

import json
import re
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

_DEFAULT_ZIPKIN_PORT = 9411

# Zipkin span kinds → OTLP ``SpanKind`` numbers; a span without ``kind``
# (every INTERNAL span) is 1.
_OTLP_KIND = {"SERVER": 2, "CLIENT": 3, "PRODUCER": 4, "CONSUMER": 5}

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

_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+(e[+-]?\d+)?$", re.IGNORECASE)
# Keys whose digit-only values are identifiers, never numbers.
_ID_KEY_SUFFIXES = (".id", "_id", "id")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def coerce_tag(key: str, value: Any) -> Any:
    """Undo Zipkin's stringification where it is safe: ``"7589"`` → 7589,
    ``"1442.3"`` → 1442.3, ``"true"`` → True. A digit-only value on an id-like
    key (``session_id``, ``service.instance.id``) stays a string."""
    if not isinstance(value, str):
        return value
    if value == "true":
        return True
    if value == "false":
        return False
    if key.endswith(_ID_KEY_SUFFIXES):
        return value
    if _INT_RE.match(value):
        try:
            return int(value)
        except ValueError:
            return value
    if _FLOAT_RE.match(value):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def parse_annotation(value: Any) -> Tuple[str, Dict[str, Any]]:
    """``"name"`` → (name, {}); ``"name":{"k":"v"}`` → (name, {k: v})."""
    text = str(value or "")
    if text.startswith('"'):
        try:
            obj = json.loads("{" + text + "}")
        except (TypeError, ValueError):
            obj = None
        if isinstance(obj, dict) and len(obj) == 1:
            name, attrs = next(iter(obj.items()))
            return str(name), dict(attrs) if isinstance(attrs, dict) else {"value": attrs}
    return text, {}


def annotation_query(tags: Dict[str, Any]) -> str:
    """Zipkin's ``annotationQuery`` grammar: ``key=value`` pairs joined by
    `` and ``. Verified on zipkin-otel 0.3.0 (Zipkin 3.6.0): values may
    contain ``/`` and ``.``; there is no quoting, so a value containing
    `` and `` cannot be expressed."""
    return " and ".join(f"{k}={v}" for k, v in tags.items())


@register
class ZipkinAdapter(BackendAdapter):
    handles = frozenset({"zipkin"})
    query_lang_label = "Zipkin annotationQuery (key=value)"
    raw_placeholder = "tool.name=terminal otel.status_code=ERROR"
    filter_support = {
        "service": "server",
        "name": "client",  # ``spanName=`` is exact; a prefix is checked on the rows
        "model": "server",
        "session": "server",
        "tool": "server",
        "min_duration": "server",
        "status_error": "server",
        "status_ok": "none",  # no negative tag search
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
        port = cfg.get("query_port") or parsed.port or _DEFAULT_ZIPKIN_PORT
        self.query_url = f"{scheme}://{rewrite_host_for_docker(host)}:{port}"
        self.api_key = resolve_env_or_literal(cfg, "api_key", "api_key_env")
        self.default_service = default_service_name(cfg)

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        base["default_service"] = self.default_service
        return base

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    # ── Filter translation ───────────────────────────────────────────

    def _tags(self, f: StructuredFilter) -> Dict[str, Any]:
        tags: Dict[str, Any] = dict(f.attr_equals)
        tags.update(parse_kv_tokens(f.raw))
        if f.status == "error":
            tags["otel.status_code"] = "ERROR"
        return tags

    def _build_query(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> List[Tuple[str, str]]:
        end_ms = int(end_s) * 1000
        if f.before_ns:
            # The cursor is the query's end bound (ms); strictly_older_traces()
            # makes the cut exact afterwards.
            end_ms = min(end_ms, int(f.before_ns) // 1_000_000)
        lookback_ms = max(1, end_ms - int(start_s) * 1000)
        params: List[Tuple[str, str]] = [
            ("serviceName", f.service or self.default_service),
            ("endTs", str(end_ms)),
            ("lookback", str(lookback_ms)),
            # One beyond the page so has_more is exact.
            ("limit", str(int(limit) + 1)),
        ]
        if f.name_regex:
            params.append(("spanName", f.name_regex))
        if f.min_duration_ms and f.min_duration_ms > 0:
            params.append(("minDuration", str(int(f.min_duration_ms) * 1000)))
        tags = self._tags(f)
        if tags:
            params.append(("annotationQuery", annotation_query(tags)))
        return params

    # ── Shape translation ────────────────────────────────────────────

    @staticmethod
    def _attrs(span: Dict[str, Any]) -> Dict[str, Any]:
        tags = span.get("tags") or {}
        return {k: coerce_tag(k, v) for k, v in tags.items() if isinstance(k, str)}

    @staticmethod
    def _find_root(spans: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        ids = {sp.get("id") for sp in spans}
        for sp in spans:
            parent = sp.get("parentId")
            if not parent or parent not in ids:
                return sp
        return spans[0] if spans else None

    def _root_matches(
        self, root: Dict[str, Any], attrs: Dict[str, Any], f: StructuredFilter
    ) -> bool:
        """Whether the ROOT span itself satisfies the filter: ``annotationQuery``
        and ``spanName`` match any span of a trace, so roots-only re-checks the
        same predicates on the root (#295)."""
        if root.get("parentId"):
            return False  # an orphan: its real root is outside the window
        name = root.get("name") or ""
        if f.name_prefix and not name.startswith(f.name_prefix):
            return False
        if f.name_regex and name != f.name_regex:
            return False
        for k, v in self._tags(f).items():
            have = attrs.get(k)
            if have is None or str(have).lower() != str(v).lower():
                return False
        if f.min_duration_ms and _int(root.get("duration")) < int(f.min_duration_ms) * 1000:
            return False
        return True

    @staticmethod
    def _status_of(attrs: Dict[str, Any]) -> Dict[str, Any]:
        """OTLP status from the ``otel.status_code`` tag the zipkin-otel module
        writes (``OK`` / ``ERROR``); on ERROR the ``error`` tag holds the
        status message."""
        code = attrs.get("otel.status_code")
        error = attrs.get("error")
        message = str(error) if isinstance(error, str) else ""
        if isinstance(code, str) and code:
            return otlp_status(code, message)
        return otlp_status("error" if error not in (None, False, "") else "ok", message)

    @staticmethod
    def _events(span: Dict[str, Any]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for ann in span.get("annotations") or []:
            if not isinstance(ann, dict):
                continue
            name, attrs = parse_annotation(ann.get("value"))
            events.append(
                {
                    "name": name,
                    "timeUnixNano": str(_int(ann.get("timestamp")) * 1000),
                    "attributes": otlp_attrs_from_dict(attrs),
                }
            )
        return events

    # ── Public API ───────────────────────────────────────────────────

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        params = self._build_query(f, start_s, end_s, limit)
        url = f"{self.query_url}/api/v2/traces?{_urlparse.urlencode(params)}"
        data = http_get_json(url, headers=self._headers(), timeout=15.0)
        items = data if isinstance(data, list) else []

        traces: List[Dict[str, Any]] = []
        for spans in items:
            if not isinstance(spans, list):
                continue
            spans = [sp for sp in spans if isinstance(sp, dict)]
            if not spans:
                continue
            trace_id = spans[0].get("traceId")
            root = self._find_root(spans)
            if not trace_id or root is None:
                continue
            attrs = self._attrs(root)
            if f.roots_only and not self._root_matches(root, attrs, f):
                continue
            card = root
            if not f.roots_only and f.name_prefix:
                # Widened to every span (the kind filter): the card is the
                # first span carrying the prefix, as the other adapters show it.
                card = next(
                    (
                        sp
                        for sp in sorted(spans, key=lambda sp: _int(sp.get("timestamp")))
                        if str(sp.get("name") or "").startswith(f.name_prefix)
                    ),
                    None,
                )
                if card is None:
                    continue
                attrs = self._attrs(card)
            service = ((card.get("localEndpoint") or {}).get("serviceName")) or ""
            keep = {k: v for k, v in attrs.items() if k in _CARD_TAGS}
            keep["name"] = card.get("name") or ""
            if self._status_of(attrs).get("code") == 2:
                keep["status"] = "error"
            start_us = _int(card.get("timestamp"))
            dur_us = _int(card.get("duration"))
            traces.append(
                {
                    "traceID": trace_id,
                    "spanCount": len(spans),
                    "rootServiceName": service,
                    "rootTraceName": card.get("name") or "",
                    "startTimeUnixNano": str(start_us * 1000) if start_us else "0",
                    "durationMs": dur_us // 1000 if dur_us else 0,
                    "spanSets": [
                        {
                            "spans": [
                                {
                                    "spanID": card.get("id"),
                                    "name": card.get("name") or "",
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
        return f"{self.query_url}/zipkin/traces/{trace_id}"

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        url = f"{self.query_url}/api/v2/trace/{_urlparse.quote(trace_id)}"
        try:
            data = http_get_json(url, headers=self._headers(), timeout=20.0)
        except BackendError as exc:
            if exc.kind == "not_found":
                raise BackendError(404, f"Trace {trace_id} not found in Zipkin", "not_found")
            raise
        spans = [sp for sp in (data if isinstance(data, list) else []) if isinstance(sp, dict)]
        if not spans:
            raise BackendError(404, f"Trace {trace_id} not found in Zipkin", "not_found")

        by_service: Dict[str, List[Dict[str, Any]]] = {}
        for sp in spans:
            attrs = self._attrs(sp)
            service = ((sp.get("localEndpoint") or {}).get("serviceName")) or ""
            start_us = _int(sp.get("timestamp"))
            dur_us = _int(sp.get("duration"))
            otlp_span = {
                "traceId": sp.get("traceId") or trace_id,
                "spanId": sp.get("id"),
                "parentSpanId": sp.get("parentId") or None,
                "name": sp.get("name") or "",
                "kind": _OTLP_KIND.get(str(sp.get("kind") or "").upper(), 1),
                "startTimeUnixNano": str(start_us * 1000) if start_us else "0",
                "endTimeUnixNano": str((start_us + dur_us) * 1000) if start_us else "0",
                "attributes": otlp_attrs_from_dict(attrs),
                "status": self._status_of(attrs),
            }
            events = self._events(sp)
            if events:
                otlp_span["events"] = events
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
