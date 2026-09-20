"""Distributed trace-context propagation (W3C ``traceparent``).

Downstream services (notably MCP servers reached over HTTP) can be linked
into the agent's trace by forwarding the active span's context as a W3C
``traceparent`` header. The span registry (``tracer.spans``) is a
process-global registry keyed by ``session_id`` holding real OTel spans, so
these helpers are safe to call from any thread or event loop — including the
MCP background loop, which does not inherit the agent task's OTel context var.

:func:`get_current_traceparent` is public API (imported by integrations and
tests). The ``mcp_request_headers`` hook is registered only on a Hermes that
advertises it; no release does today (#130).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ._common import _fail_open, _resolve_session_id, get_tracer


def get_current_traceparent(session_id: Optional[str] = None) -> Optional[str]:
    """Return the W3C ``traceparent`` for the active span, or ``None``.

    Format: ``00-<32-hex trace id>-<16-hex span id>-<2-hex flags>`` (the
    ``traceparent`` header defined by https://www.w3.org/TR/trace-context/).

    Pass ``session_id`` when you know it — the lookup is session-keyed and
    survives thread/event-loop boundaries. With no ``session_id`` (or an
    unknown one) it falls back to the current context-var parent and, as a
    last resort, the innermost span of any single active session. Returns
    ``None`` when tracing is disabled or no valid span is active.
    """
    tracer = get_tracer()
    spans = getattr(tracer, "spans", None)
    if spans is None:
        return None

    parent = spans.get_current_parent(session_id)
    if parent is None:
        # Last resort: any active session's innermost span. Helps callers on a
        # background loop (e.g. an outbound MCP request) that can't supply a
        # session_id when exactly one session is in flight.
        stacks = getattr(spans, "_session_parent_stacks", {})
        for stack in reversed(list(stacks.values())):
            if stack:
                parent = stack[-1]
                break
    if parent is None:
        return None

    get_ctx = getattr(parent, "get_span_context", None)
    if get_ctx is None:
        return None
    ctx = get_ctx()
    if ctx is None or not getattr(ctx, "is_valid", False):
        return None

    flags = "01" if (ctx.trace_flags & 1) else "00"
    return f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{flags}"


@_fail_open
def on_mcp_request_headers(
    server_name: Optional[str] = None,
    tool_name: Optional[str] = None,
    session_id: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, str]:
    """``mcp_request_headers`` hook — inject ``traceparent`` on outbound MCP calls.

    Returns a header dict merged onto the outbound MCP HTTP request by
    hermes-agent. Empty dict when no trace is active (never raises, never
    blocks the call). ``session_id`` may also arrive nested in ``kwargs``.
    """
    sid = _resolve_session_id(kwargs, session_id=session_id)
    traceparent = get_current_traceparent(sid or None)
    return {"traceparent": traceparent} if traceparent else {}
