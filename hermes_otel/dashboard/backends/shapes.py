"""The two shapes every adapter answers with, built in one place (#403).

* a **card row** — one entry of ``search()``'s ``traces`` list, the
  Tempo-style ``{traceID, spanCount, rootServiceName, rootTraceName,
  startTimeUnixNano, durationMs, spanSets}`` the trace list renders;
* an **OTLP span** and the **batches** ``get_trace()`` returns, the OTLP
  JSON ``{batches: [{resource, scopeSpans: [{spans}]}]}`` the span tree
  consumes.

Plus the small decoders every adapter needs on the way: an OTLP ``AnyValue``
unwrapped, an attribute list as a dict, a nested attribute object flattened
to dotted keys, a tolerant integer.

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
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Union

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
    """A numeric field (``startTime``, ``duration``), 0 when malformed."""
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


def group_by_service(
    spans: Iterable[Dict[str, Any]], service_of: Callable[[Dict[str, Any]], Any]
) -> Dict[str, List[Dict[str, Any]]]:
    """Spans grouped by the service ``service_of`` names for each (``""`` when
    unknown), first-seen order."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for sp in spans:
        out.setdefault(str(service_of(sp) or ""), []).append(sp)
    return out


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
