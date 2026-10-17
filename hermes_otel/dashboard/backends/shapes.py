"""The two shapes every adapter answers with, built in one place (#403).

* a **card row** — one entry of ``search()``'s ``traces`` list, the
  Tempo-style ``{traceID, spanCount, rootServiceName, rootTraceName,
  startTimeUnixNano, durationMs, spanSets}`` the trace list renders;
* an **OTLP span** and the **batches** ``get_trace()`` returns, the OTLP
  JSON ``{batches: [{resource, scopeSpans: [{spans}]}]}`` the span tree
  consumes.

Plus the small decoders every adapter needs on the way: an OTLP ``AnyValue``
unwrapped, an attribute list as a dict, a nested attribute object flattened
to dotted keys, a tolerant integer, a span kind in any spelling.

Between the two sits :class:`SpanRow` (#513): the facts an adapter decodes
its native span into. :func:`card_from_trace` turns a trace's rows into its
list row (the root or the matched span, the orphan and predicate re-checks),
:func:`trace_detail` turns them into the batches; an adapter writes only the
decoder.

Thirteen adapters used to carry their own copy of the card-key table and of
the row literal, and they had drifted (``spanCount`` missing here, ``matched``
meaning two things there). The rules are now these:

* ``CARD_ATTRIBUTE_KEYS`` is the one list of attributes a card carries. An
  adapter that reads a backend-specific source (Langfuse's ``metadata``)
  may read fewer; none invents its own spelling.
* ``spanCount`` is the number of spans in the whole trace, present only when
  the backend told the adapter (a count query, a ``numSpans`` field, the
  spans of a fetched trace). It is never the number of matched spans.
* ``matched`` is that same count when known, else ``1`` (the card's one
  span): what the UI shows next to a card's span set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

# The attributes a trace-list card carries, in the order the adapters ask a
# backend for them (the TraceQL ``select()``, SigNoz's ``selectColumns``).
CARD_ATTRIBUTE_KEYS = (
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
)
CARD_ATTRIBUTE_SET = frozenset(CARD_ATTRIBUTE_KEYS)


def card_attrs(attrs: Dict[str, Any], name: str = "", error: bool = False) -> Dict[str, Any]:
    """The card's attribute dict: the known keys of ``attrs``, the span's
    ``name`` and ``status: error`` when the span failed."""
    keep = {k: v for k, v in attrs.items() if k in CARD_ATTRIBUTE_SET}
    keep["name"] = name or ""
    if error:
        keep["status"] = "error"
    return keep


def to_int(value: Any) -> int:
    """A numeric field (``startTime``, ``duration``), 0 when malformed.

    Not :func:`hermes_otel.coerce.to_int`: OTLP JSON carries nanosecond
    timestamps as 19-digit strings, which ``int(text)`` keeps exact and a
    round trip through ``float`` would not (#522).
    """
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


# ── OTLP JSON decoding ─────────────────────────────────────────────────


def otlp_unwrap(value: Any) -> Any:
    """An OTLP JSON ``AnyValue`` (``{"stringValue": ...}``) → the Python value;
    ``intValue`` arrives as a digit string and comes back as an int."""
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
        return [otlp_unwrap(v) for v in (value["arrayValue"] or {}).get("values") or []]
    if "kvlistValue" in value:
        return {
            kv.get("key"): otlp_unwrap(kv.get("value"))
            for kv in (value["kvlistValue"] or {}).get("values") or []
        }
    return value


def otlp_attrs_to_dict(items: Any) -> Dict[str, Any]:
    """An OTLP attribute list (``[{key, value}]``) → a flat dict."""
    return {
        a["key"]: otlp_unwrap(a.get("value"))
        for a in (items or [])
        if isinstance(a, dict) and a.get("key")
    }


def flatten_dotted(obj: Any, prefix: str = "", keep_empty: bool = False) -> Dict[str, Any]:
    """A nested attribute object (``{"llm": {"model_name": ...}}``) → dotted
    keys (``{"llm.model_name": ...}``). An empty nested object is dropped
    unless ``keep_empty`` (then it is kept as the value ``{}``)."""
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and (v or not keep_empty):
                out.update(flatten_dotted(v, key, keep_empty))
            else:
                out[key] = v
    elif prefix:
        out[prefix] = obj
    return out


# ── OTLP JSON encoding (what get_trace answers) ────────────────────────


def otlp_attr(key: str, value: Any) -> Dict[str, Any]:
    """Wrap ``(key, value)`` into an OTLP attribute entry."""
    if value is None:
        return {"key": key, "value": {}}
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    if isinstance(value, str):
        return {"key": key, "value": {"stringValue": value}}
    # dict / list / anything else: stringify as JSON so the UI can still
    # render it. Preserves structure in a predictable form.
    try:
        return {"key": key, "value": {"stringValue": json.dumps(value, default=str)}}
    except Exception:
        return {"key": key, "value": {"stringValue": str(value)}}


def otlp_attrs_from_dict(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Render a flat dict as a list of OTLP attribute entries."""
    return [otlp_attr(k, v) for k, v in d.items() if v is not None]


def otlp_status(code: Union[str, int, None], message: str = "") -> Dict[str, Any]:
    """Normalize various status flavours into an OTLP ``Status`` message."""
    if code is None:
        return {}
    if isinstance(code, int):
        return {"code": code, "message": message or ""}
    norm = str(code).lower()
    if norm in ("error", "status_code_error", "err", "2"):
        return {"code": 2, "message": message or "error"}
    if norm in ("ok", "status_code_ok", "success", "1"):
        return {"code": 1, "message": message or ""}
    return {"code": 0, "message": message or ""}


def otlp_span(
    trace_id: Any,
    span_id: Any,
    parent_id: Any,
    name: Any,
    *,
    start_ns: int,
    end_ns: int,
    attrs: Dict[str, Any],
    status: Dict[str, Any],
    kind: int = 1,
    events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """One span of the detail tree. A zero start or end is spelled ``"0"``;
    ``kind`` is the OTLP transport kind (1, INTERNAL, for every backend whose
    kinds are semantic); ``events`` is only set when there are some."""
    span: Dict[str, Any] = {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_id or None,
        "name": name or "",
        "kind": kind,
        "startTimeUnixNano": str(start_ns) if start_ns else "0",
        "endTimeUnixNano": str(end_ns) if end_ns else "0",
        "attributes": otlp_attrs_from_dict(attrs),
        "status": status,
    }
    if events:
        span["events"] = events
    return span


def otlp_event(name: Any, time_ns: Any, attrs: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": str(name or ""),
        "timeUnixNano": str(time_ns or 0),
        "attributes": otlp_attrs_from_dict(attrs),
    }


def otlp_batch(resource_attrs: Dict[str, Any], spans: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "resource": {"attributes": otlp_attrs_from_dict(resource_attrs)},
        "scopeSpans": [{"spans": spans}],
    }


def otlp_batches(by_service: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """One batch per service, ``service.name`` as its only resource attribute
    (none when the service is unknown), in insertion order."""
    return [
        otlp_batch({"service.name": service} if service else {}, spans)
        for service, spans in by_service.items()
    ]


def span_count_of(batches: Sequence[Dict[str, Any]]) -> int:
    """The spans in an OTLP ``batches`` list."""
    return sum(
        len((scope or {}).get("spans") or [])
        for b in batches or []
        if isinstance(b, dict)
        for scope in b.get("scopeSpans") or []
    )


# ── the trace-list card ────────────────────────────────────────────────


def card_span_set(
    span_id: Any, name: Any, attrs: Dict[str, Any], span_count: Optional[int] = None
) -> Dict[str, Any]:
    """The row's one span set: the card span and ``matched`` (see the module
    docstring: the trace's span count when known, else 1)."""
    return {
        "spans": [
            {"spanID": span_id, "name": name or "", "attributes": otlp_attrs_from_dict(attrs)}
        ],
        "matched": int(span_count) if span_count else 1,
    }


def card_row(
    trace_id: Any,
    *,
    span_id: Any,
    name: Any,
    attrs: Dict[str, Any],
    start_ns: int,
    duration_ms: int,
    service: Any = "",
    span_count: Optional[int] = None,
) -> Dict[str, Any]:
    """One trace-list row from its card span.

    ``attrs`` is the card's attribute dict (see :func:`card_attrs`: the known
    keys plus ``name`` and ``status``); ``span_count`` the whole trace's span
    count when the backend told it, else ``None`` (the key is then absent and
    ``matched`` is 1).
    """
    row: Dict[str, Any] = {"traceID": trace_id}
    if span_count is not None:
        row["spanCount"] = int(span_count)
    row.update(
        {
            "rootServiceName": str(service or ""),
            "rootTraceName": name or "",
            "startTimeUnixNano": str(start_ns) if start_ns else "0",
            "durationMs": int(duration_ms),
            "spanSets": [card_span_set(span_id, name, attrs, span_count)],
        }
    )
    return row


# ── one typed span: what every adapter decodes its native span into ─────

_SPAN_KINDS = {"internal": 1, "server": 2, "client": 3, "producer": 4, "consumer": 5}


def otlp_kind(value: Any) -> int:
    """A span kind in any backend's spelling → the OTLP ``SpanKind`` number:
    ``2``, ``"2"``, ``"SERVER"``, ``"Server"``, ``"server"`` and
    ``"SPAN_KIND_SERVER"`` are all 2. Unknown or missing is 1 (INTERNAL)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value or 1
    text = str(value or "").strip().lower()
    if text.isdigit():
        return int(text) or 1
    if text.startswith("span_kind_"):
        text = text[len("span_kind_") :]
    return _SPAN_KINDS.get(text, 1)


@dataclass(frozen=True)
class SpanRow:
    """The facts a card or a detail span is built from, whatever the backend
    called them (#513). An adapter's ``_row(sp)`` decodes its native span
    into one; :func:`card_from_trace` and :func:`trace_detail` do the rest.

    ``status`` is the OTLP status (see :func:`otlp_status`) and ``error``
    reads it: one spelling of "this span failed". ``kind`` is the OTLP
    transport kind (see :func:`otlp_kind`; 1 for a backend whose kinds are
    semantic). ``resource`` is the batch the detail puts the span in; ``None``
    means ``{"service.name": service}`` (nothing when the service is unknown).
    """

    trace_id: Any
    span_id: Any
    parent_id: Any
    name: str
    start_ns: int
    end_ns: int
    attrs: Dict[str, Any]
    service: str = ""
    kind: int = 1
    status: Dict[str, Any] = field(default_factory=dict)
    events: Optional[List[Dict[str, Any]]] = None
    resource: Optional[Dict[str, Any]] = None

    @property
    def error(self) -> bool:
        return self.status.get("code") == 2

    @property
    def duration_ns(self) -> int:
        return max(0, self.end_ns - self.start_ns)

    def card(self, span_count: Optional[int] = None) -> Dict[str, Any]:
        """This span as its trace's list row (see :func:`card_row`)."""
        return card_row(
            self.trace_id,
            span_id=self.span_id,
            name=self.name,
            attrs=card_attrs(self.attrs, self.name, self.error),
            start_ns=self.start_ns,
            duration_ms=self.duration_ns // 1_000_000,
            service=self.service,
            span_count=span_count,
        )


def card_from_trace(
    f: Any,
    rows: Sequence[SpanRow],
    *,
    equals: Optional[Dict[str, Any]] = None,
    check_all: bool = False,
    server_roots: bool = False,
) -> Optional[Dict[str, Any]]:
    """One trace's list row, built from the span the filter ``f`` (a
    ``StructuredFilter``) picks among ``rows``, the trace's spans; ``None``
    when the trace has no card under the filter.

    Roots-only: the card is the root (the first row whose parent is absent or
    not among the rows). The backend may have matched ANY span of the trace
    (Jaeger's ``tags=``, Zipkin's ``annotationQuery``, #295), so the root
    must itself satisfy the predicates, and an orphan (its real root outside
    the window) is no root. ``server_roots`` says the backend answered
    roots-only itself (Tempo's ``nestedSetParent < 0`` with every predicate
    on the root): the root is the card as the detail shows it, orphan or not,
    nothing re-checked.

    Widened by the kind filter: the card is the first span by start carrying
    the name prefix, as the other backends show it (the root without one).

    ``equals`` replaces ``f.attr_equals`` (an adapter that folded its raw
    tags into one dict). ``check_all`` is for a backend that applied no
    predicate (Aspire's API has no filters) or matched a span its answer does
    not name (Gigapipe's span set): every predicate is checked here, status
    and free text included, on the root (roots-only) or on every span by
    start (widened) until one satisfies them all.
    """
    from .base import root_of, span_matches  # base imports this module's emitters

    if not rows:
        return None
    root = root_of(rows, lambda r: r.span_id, lambda r: r.parent_id)  # type: ignore[arg-type]

    def ok(r: SpanRow) -> bool:
        text = f"{r.attrs.get('input.value', '')}\n{r.attrs.get('output.value', '')}"
        return span_matches(
            f,
            name=r.name,
            attrs=r.attrs,
            duration_ns=r.duration_ns,
            error=r.error if check_all else None,
            text=text if check_all else None,
            equals=equals,
        )

    by_start = sorted(rows, key=lambda r: r.start_ns)
    if f.roots_only:
        if server_roots:
            card = root
        elif root.parent_id:
            return None
        else:
            card = root if ok(root) else None
    elif check_all:
        card = next((r for r in by_start if ok(r)), None)
    elif f.name_prefix:
        card = next((r for r in by_start if r.name.startswith(f.name_prefix)), None)
    else:
        card = root
    return card.card(len(rows)) if card is not None else None


def trace_detail(rows: Sequence[SpanRow], *, truncated: bool = False) -> Dict[str, Any]:
    """What ``get_trace()`` answers: the rows as OTLP batches, one per
    resource in first-seen order (see :attr:`SpanRow.resource`),
    ``span_count`` their number and ``truncated`` as the caller says."""
    groups: Dict[str, Tuple[Dict[str, Any], List[Dict[str, Any]]]] = {}
    for r in rows:
        if r.resource is not None:
            resource = r.resource
        else:
            resource = {"service.name": r.service} if r.service else {}
        key = json.dumps(resource, sort_keys=True, default=str)
        groups.setdefault(key, (resource, []))[1].append(
            otlp_span(
                r.trace_id,
                r.span_id,
                r.parent_id,
                r.name,
                start_ns=r.start_ns,
                end_ns=r.end_ns,
                attrs=r.attrs,
                status=r.status,
                kind=r.kind,
                events=r.events,
            )
        )
    return {
        "batches": [otlp_batch(resource, spans) for resource, spans in groups.values()],
        "span_count": len(rows),
        "truncated": truncated,
    }
