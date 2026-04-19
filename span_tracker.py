"""Active-span registry + parent-span stack for the hermes-otel plugin.

Hermes fires ``pre_*`` and ``post_*`` hooks independently; between the
two we need to keep a handle on the currently-open span so the ``post_*``
hook can end it. :class:`SpanTracker` is that registry, plus the parent
stack that lets tool spans nest correctly under whichever LLM / API
call is currently in flight.

Two parent stacks run in parallel:

* ``_session_parent_stacks`` — a plain ``dict`` keyed by ``session_id``.
  Primary source of parent context. hermes-agent dispatches hooks across
  threads / async tasks; a :class:`~contextvars.ContextVar` alone cannot
  carry the session span from ``on_session_start`` into subsequent hooks
  when those hooks fire on different workers. The session-keyed stack is
  shared state (Python's GIL makes ``dict`` / ``list`` ops atomic), so
  any hook with a ``session_id`` can recover the current parent no
  matter which thread runs it.

* ``_PARENT_STACK`` ContextVar — fallback for hooks that fire without
  a ``session_id`` (e.g. synthetic test calls) and for keeping nesting
  correct inside a single task when multiple sessions share a worker
  thread. Isolated per-task / per-thread so concurrent sessions do
  not cross-contaminate.

:meth:`SpanTracker.get_current_parent` prefers the session-keyed stack
and falls back to the ContextVar.
"""

from __future__ import annotations

import contextvars
from typing import Any, Dict, Optional

# Imported lazily — SpanTracker's end_span() only needs these when a
# real span is being closed, but importing here keeps the module
# self-contained and fails fast if OTel is missing.
try:
    from opentelemetry.trace import Status, StatusCode

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover — the plugin is unusable without OTel
    _OTEL_AVAILABLE = False
    Status = None  # type: ignore[assignment]
    StatusCode = None  # type: ignore[assignment]


# Per-context parent span stack. Using ContextVar (not threading.local)
# ensures isolation across both threads AND asyncio coroutines: each
# async task and each thread gets its own independent stack because
# contextvars copy-on-write at task / thread boundaries.
#
# Default is None (not []) to avoid sharing a single list across contexts.
_PARENT_STACK: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar(
    "hermes_otel_parent_stack", default=None
)


class SpanTracker:
    """Active-span registry + parent-span stack. See module docstring."""

    def __init__(self):
        # key = f"{tool_name}:{task_id}" or f"llm:{session_id}" or f"session:{session_id}"
        self._active_spans: Dict[str, Any] = {}
        # session_id -> [parent, ...]. Lives in plain memory so every
        # thread / task that handles a hook for this session sees the
        # same stack. See module docstring for rationale.
        self._session_parent_stacks: Dict[str, list] = {}

    def _parent_stack(self) -> list:
        """Return this context's parent span stack, creating it if needed."""
        stack = _PARENT_STACK.get()
        if stack is None:
            stack = []
            _PARENT_STACK.set(stack)
        return stack

    def start_span(self, key: str, span) -> None:
        """Store an active span by key."""
        self._active_spans[key] = span

    def push_parent(self, span, session_id: Optional[str] = None) -> None:
        """Mark ``span`` as the current parent.

        When ``session_id`` is provided the span is also pushed onto the
        session-keyed stack so hooks on a different thread / task for
        the same session still see it.
        """
        self._parent_stack().append(span)
        if session_id:
            self._session_parent_stacks.setdefault(session_id, []).append(span)

    def pop_parent(self, session_id: Optional[str] = None) -> None:
        """Remove the current parent span.

        Pops both the ContextVar stack (best-effort — may be empty if
        the pop lands on a different thread than the push) and the
        session-keyed stack when a ``session_id`` is given.
        """
        stack = self._parent_stack()
        if stack:
            stack.pop()
        if session_id:
            s = self._session_parent_stacks.get(session_id)
            if s:
                s.pop()
                if not s:
                    self._session_parent_stacks.pop(session_id, None)

    def get_current_parent(self, session_id: Optional[str] = None):
        """Return the current parent span, or None.

        Prefers the session-keyed stack (survives thread boundaries).
        Falls back to the ContextVar stack for callers that don't know
        the ``session_id``.
        """
        if session_id:
            s = self._session_parent_stacks.get(session_id)
            if s:
                return s[-1]
        stack = self._parent_stack()
        return stack[-1] if stack else None

    def end_span(
        self,
        key: str,
        attributes: Optional[dict] = None,
        status: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """End and remove a tracked span.

        Args:
            key: The tracking key for the span.
            attributes: Final attributes to set before ending.
            status: ``"ok"`` or ``"error"``. ``None`` skips status-setting.
            error_message: Description attached when ``status == "error"``.
        """
        span = self._active_spans.pop(key, None)
        if not span:
            return

        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)

        if status == "error":
            span.set_status(Status(status_code=StatusCode.ERROR, description=error_message or ""))
        elif status == "ok":
            # Set an explicit empty description so backends don't render "None".
            span.set_status(Status(status_code=StatusCode.OK, description=""))

        span.end()

    def get_span(self, key: str):
        """Get an active span by key."""
        return self._active_spans.get(key)

    def end_all(self) -> None:
        """End all remaining spans (cleanup).

        Only clears this context's parent stack — other tasks / threads
        are untouched.
        """
        for key in list(self._active_spans.keys()):
            self.end_span(key)
        self._active_spans.clear()
        self._session_parent_stacks.clear()
        stack = _PARENT_STACK.get()
        if stack is not None:
            stack.clear()
