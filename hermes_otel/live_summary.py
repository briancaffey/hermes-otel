"""Pure trace arithmetic over stored span dicts: the trace-list row the live
store, the dashboard API and the query CLI all build (``summarize_trace``),
its root, totals and delegation roll-ups.

No SQLite here (#518): everything works on the span dicts the store returns,
so the query CLI can summarise a backend's spans the same way.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import semconv as _sc
from .coerce import optional_number

# The vocabulary lives in ``semconv`` (#404); these names stay for the readers.
_SESSION_KEYS = _sc.SESSION_KEYS
_MODEL_KEYS = _sc.MODEL_KEYS
_TOKEN_KEYS = _sc.TOTAL_TOKEN_KEYS
_COST_KEYS = _sc.COST_KEYS
# A delegation span's roll-up of the child's whole subtree (#390); the parent's
# root keeps only the parent's own calls.
_SUB_TOKENS = "hermes.subagent.usage.total_tokens"
_SUB_CALLS = "hermes.subagent.api_call_count"
_SUB_COST = "hermes.subagent.cost.usage"

_first = _sc.first


def _session_of(attrs: Dict[str, Any]) -> Optional[str]:
    v = _first(attrs, _SESSION_KEYS)
    return str(v) if v is not None else None


def span_kind(name: str, attrs: Optional[Dict[str, Any]] = None) -> str:
    """The registry's classification (``semconv.span_kind``; ``kindOf`` in ``lib.ts``)."""
    return _sc.span_kind(name, attrs)


def root_of(spans: Sequence[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], bool]:
    """The trace's root span and whether the trace is still **partial**.

    The root is the span with no parent at all (the turn's ``agent`` /
    ``cron`` span). A span whose parent is merely absent from the store is
    *not* promoted to root: the root ends last by construction, so while a
    turn is in flight every stored span is a child. Such a trace is reported
    as partial with its earliest span standing in, so a running turn is never
    summarised as "a tool call named terminal" with a child's token count
    (#291).
    """
    spans = list(spans)
    if not spans:
        return None, True
    root = next((s for s in spans if not s.get("parent_span_id")), None)
    if root is not None:
        return root, False
    ids = {s.get("span_id") for s in spans}
    # Every parent is present except the one(s) above the stored set: the
    # earliest orphan is the best stand-in for the list row.
    orphans = [s for s in spans if s.get("parent_span_id") not in ids]
    pool = orphans or spans
    return min(pool, key=lambda s: int(s.get("start_time_unix_nano") or 0)), True


def trace_totals(spans: Iterable[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Tokens and cost for ONE trace, counted once (#178).

    The ``agent`` root already carries the turn's totals and every ``api.*``
    span carries its own call, so summing all spans doubled every turn. Use
    the root's figure when it has one; otherwise sum the ``api.*`` spans only
    (``llm.*`` spans mirror the API spans). Mirrors ``traceTotals`` in
    ``dashboard-ui/src/lib.ts``. A partial trace (no root yet) always sums
    the ``api.*`` spans.
    """
    spans = list(spans)
    root, partial = root_of(spans)
    if partial:
        root = None

    def pick(keys: Sequence[str]) -> Optional[float]:
        if root is not None:
            v = optional_number(_first(root.get("attributes") or {}, keys))
            if v is not None:
                return v
        total, seen = 0.0, False
        for s in spans:
            if not str(s.get("name", "")).startswith("api."):
                continue
            # Auxiliary attempts (#389) are reported on the root as
            # hermes.turn.auxiliary.*, never inside the turn's totals.
            if (s.get("attributes") or {}).get("hermes.query_source") == "auxiliary":
                continue
            v = optional_number(_first(s.get("attributes") or {}, keys))
            if v is not None:
                total += v
                seen = True
        return total if seen else None

    # A root with a cost status but no amount (unknown, included or partial
    # pricing) has no total; never fall back to a subtotal (#252).
    root_attrs = (root.get("attributes") or {}) if root is not None else {}
    no_cost_total = (
        "hermes.cost.status" in root_attrs
        and optional_number(_first(root_attrs, _COST_KEYS)) is None
    )
    return {"tokens": pick(_TOKEN_KEYS), "cost": None if no_cost_total else pick(_COST_KEYS)}


def top_level_subagents(spans: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The trace's delegation spans that no other delegation in it made (#447).

    A nested orchestrator's grandchildren are already inside its child's
    roll-up, so only the outermost ``subagent.*`` spans are summed. A
    delegation is nested when its parent session is another delegation's
    child session.
    """
    subs = [
        s for s in spans if span_kind(str(s.get("name") or ""), s.get("attributes")) == "subagent"
    ]
    children = {
        str(c)
        for s in subs
        for c in [(s.get("attributes") or {}).get("hermes.subagent.child_session_id")]
        if c
    }
    return [
        s
        for s in subs
        if str((s.get("attributes") or {}).get("hermes.subagent.parent_session_id") or "")
        not in children
    ]


def subagent_totals(spans: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """What the turn's top-level delegations cost: count, tokens, API calls, USD.

    ``None`` when the trace delegated nothing. A figure no delegation reported
    is ``None``; the cost is an amount only when every delegation has one (the
    turn root's pricing rule), never a partial sum.
    """
    top = top_level_subagents(spans)
    if not top:
        return None

    def total(key: str) -> Optional[float]:
        vals = [optional_number((s.get("attributes") or {}).get(key)) for s in top]
        known = [v for v in vals if v is not None]
        return sum(known) if known else None

    costs = [optional_number((s.get("attributes") or {}).get(_SUB_COST)) for s in top]
    return {
        "count": len(top),
        "tokens": total(_SUB_TOKENS),
        "calls": total(_SUB_CALLS),
        "cost": (
            sum(c for c in costs if c is not None) if all(c is not None for c in costs) else None
        ),
    }


def summarize_trace(spans: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One trace-list row from its spans (the shape ``LiveTrace`` uses in the UI).

    ``partial`` is true while the trace has no root span yet (a turn in
    flight); the row then describes its earliest stored span and the totals
    are the ``api.*`` subtotal so far.
    """
    root, partial = root_of(spans)
    assert root is not None  # spans is non-empty by contract
    start = min(int(s.get("start_time_unix_nano") or 0) for s in spans)
    end = max(int(s.get("end_time_unix_nano") or s.get("start_time_unix_nano") or 0) for s in spans)
    totals = trace_totals(spans)
    rattrs = root.get("attributes") or {}
    # The delegations are reported next to the turn's own figures only when
    # the root carries those figures: without them (a partial trace, a
    # kanban.claim run) the totals already sum every api.* span, children's
    # included, so a split would count them twice.
    own = not partial and optional_number(_first(rattrs, _TOKEN_KEYS)) is not None
    model = None
    for s in spans:
        model = _first(s.get("attributes") or {}, _MODEL_KEYS)
        if model:
            break
    return {
        "traceId": root.get("trace_id"),
        "rootName": root.get("name"),
        "rootKind": span_kind(root.get("name") or "", rattrs),
        "service": rattrs.get("service.name") or "hermes",
        "startNs": start,
        "endNs": end,
        "durationMs": (end - start) / 1e6,
        "spanCount": len(spans),
        "model": model,
        "tokens": totals["tokens"],
        "cost": totals["cost"],
        "error": any(s.get("status") == "ERROR" for s in spans),
        "session": _session_of(rattrs)
        or next(
            (
                _session_of(s.get("attributes") or {})
                for s in spans
                if _session_of(s.get("attributes") or {})
            ),
            None,
        ),
        "turn": optional_number(rattrs.get("hermes.turn.number")),
        "platform": rattrs.get("hermes.platform"),
        "partial": partial,
        "subagents": subagent_totals(spans) if own else None,
    }
