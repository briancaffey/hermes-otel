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
  shared state guarded by one re-entrant lock: every mutation here is a
  multi-step read-modify-write (``setdefault(...).append``, pop-then-drop
  when empty), which the GIL does *not* make atomic.

* ``_PARENT_STACK`` ContextVar — fallback for hooks that fire without
  a ``session_id`` (e.g. synthetic test calls) and for keeping nesting
  correct inside a single task when multiple sessions share a worker
  thread. Isolated per-task / per-thread so concurrent sessions do
  not cross-contaminate.

:meth:`SpanTracker.get_current_parent` prefers the session-keyed stack
and falls back to the ContextVar.

Span keys are namespaced strings: ``session:<session_id>``,
``llm:<session_id>``, ``api:<task_id>``, ``aux:<request_id>``,
``<tool_name>:<tool_call_id>`` (the task id on a Hermes without tool-call
ids), ``approval:<session_id>:<tool_call_id>``, ``skill:<session_id>:<name>``,
``human_input:<request_id>`` and ``subagent:<child_session_id>``. The same
keys index the pending-start table (:meth:`SpanTracker.mark_started`), the
one place the plugin remembers *when* a pre-hook fired so its post-hook can
measure the run or the wait.
"""

from __future__ import annotations

import contextvars
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional

# Imported at module load on purpose: SpanTracker.end_span() needs Status /
# StatusCode, and the plugin is unusable without the OTel API anyway, so
# failing fast here beats a NameError on the first closed span.
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


SWEEP_REASONS = ("turn_end", "session_end", "turn_failed", "ttl", "shutdown")


def sweep_attributes(reason: str) -> dict:
    """The marker set on a span the plugin ended on the operation's behalf (#477).

    ``reason`` is one of :data:`SWEEP_REASONS`: ``turn_end`` (open at
    ``on_session_end``), ``session_end`` (open at finalize / reset),
    ``turn_failed`` (the turn's API attempts all failed and the next turn
    closed it), ``ttl`` (the orphan sweep) or ``shutdown``.
    """
    return {"hermes.span.ended_by": "sweep", "hermes.span.sweep_reason": reason}


class SpanTracker:
    """Active-span registry + parent-span stack. See module docstring.

    Every method that touches the shared dicts takes ``self._lock`` (a
    re-entrant lock, so ``end_all`` can call ``end_span``). The ContextVar
    stack is per-context and needs no lock.
    """

    def __init__(self):
        self._lock = threading.RLock()
        # See the module docstring for the key namespaces.
        self._active_spans: Dict[str, Any] = {}
        # session_id -> [parent, ...]. Lives in plain memory so every
        # thread / task that handles a hook for this session sees the
        # same stack. See module docstring for rationale.
        self._session_parent_stacks: Dict[str, list] = {}
        # session_id -> {skill_name: span_key}. Skills are *overlapping*
        # execution windows opened when a skill is loaded and closed at the
        # turn boundary — they are tracked here (NOT on the parent stack) so
        # several can be active at once without disturbing tool/LLM nesting.
        self._session_skill_spans: Dict[str, Dict[str, str]] = {}
        # span key -> perf_counter start, for every hook pair that measures a
        # run or a wait (tool calls, approvals, human-input requests). Flat
        # (not per-session): the keys namespace themselves.
        self._started_at: Dict[str, float] = {}
        # Raw terminal output size per tool key, recorded by the
        # transform_terminal_output observer and read back in post_tool_call.
        self._tool_output_chars: Dict[str, int] = {}
        # Sub-agent delegation registry: delegated child's session_id -> record
        # about the delegation span opened in the parent on ``subagent_start``
        # (``{"span", "context", "role", "parent_session_id"}``), so the child's
        # own root span can rejoin the parent trace on ``on_session_start``.
        # Process-local: in-process delegation parents the child root directly
        # under the delegation span (one connected trace); cross-process
        # delegation degrades to attribute-only correlation. Bounded (oldest
        # out) like the per-session tables in SessionState: a child whose
        # session never starts would otherwise pin its record for ever.
        self._subagent_registry: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        # Delegation start times, child_session_id -> perf_counter. Separate
        # from the registry, which the child's own session end may drop: the
        # delegation span itself lives until subagent_stop (#392).
        self._delegation_started_at: Dict[str, float] = {}
        # Turn registry for the orphan sweep (#405): session_id -> perf_counter
        # start time, and session_id -> the active span keys opened under it
        # (api:/tool: keys do not embed the session id), so a timed-out turn's
        # sub-spans can be finalized before its root.
        self._turn_started_at: Dict[str, float] = {}
        self._session_keys: "OrderedDict[str, set]" = OrderedDict()

    # Per-session tables keyed by a session id Hermes may never close: the
    # same cap SessionState uses for its own.
    _MAX_SESSIONS = 4096

    def _remember(self, table: "OrderedDict[str, Any]", key: str, value: Any) -> None:
        table[key] = value
        table.move_to_end(key)
        while len(table) > self._MAX_SESSIONS:
            table.popitem(last=False)

    def _parent_stack(self) -> list:
        """Return this context's parent span stack, creating it if needed."""
        stack = _PARENT_STACK.get()
        if stack is None:
            stack = []
            _PARENT_STACK.set(stack)
        return stack

    # ── Active spans ─────────────────────────────────────────────────────

    def start_span(self, key: str, span) -> None:
        """Store an active span by key."""
        with self._lock:
            self._active_spans[key] = span

    def get_span(self, key: str):
        """Get an active span by key."""
        with self._lock:
            return self._active_spans.get(key)

    def has_span(self, key: str) -> bool:
        """True when a span is tracked under ``key``."""
        with self._lock:
            return key in self._active_spans

    def discard(self, key: str):
        """Forget a tracked span without ending it; returns it (or None).

        For callers that close a span by other means than ``span.end()``.
        """
        with self._lock:
            return self._active_spans.pop(key, None)

    def active_count(self) -> int:
        with self._lock:
            return len(self._active_spans)

    # ── Parent stacks ────────────────────────────────────────────────────

    def push_parent(self, span, session_id: Optional[str] = None) -> None:
        """Mark ``span`` as the current parent.

        When ``session_id`` is provided the span is also pushed onto the
        session-keyed stack so hooks on a different thread / task for
        the same session still see it.
        """
        self._parent_stack().append(span)
        if session_id:
            with self._lock:
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
            with self._lock:
                s = self._session_parent_stacks.get(session_id)
                if s:
                    s.pop()
                    if not s:
                        self._session_parent_stacks.pop(session_id, None)

    def get_session_root(self, session_id: Optional[str] = None):
        """Return the session's *root* span (bottom of the stack), or None.

        Skill spans nest under this turn-level root rather than whatever tool /
        LLM span happens to be in flight, so a skill reads as active for the
        whole turn.
        """
        if session_id:
            with self._lock:
                s = self._session_parent_stacks.get(session_id)
                if s:
                    return s[0]
        return None

    def get_current_parent(self, session_id: Optional[str] = None):
        """Return the current parent span, or None.

        Prefers the session-keyed stack (survives thread boundaries).
        Falls back to the ContextVar stack for callers that don't know
        the ``session_id``.
        """
        if session_id:
            with self._lock:
                s = self._session_parent_stacks.get(session_id)
                if s:
                    return s[-1]
        stack = self._parent_stack()
        return stack[-1] if stack else None

    def single_active_session(self):
        """``(session_id, root_span)`` when exactly one session has a turn in
        flight, else ``None``.

        Log lines are written on the agent's threads, where the plugin's spans
        are not on the OpenTelemetry context, so ``trace.get_current_span()``
        sees nothing. With one active session (the CLI, or a quiet gateway)
        the line can be attributed to that session's root span without
        guessing; with several it stays unattributed rather than wrong.
        """
        with self._lock:
            live = [(sid, st[0]) for sid, st in self._session_parent_stacks.items() if st]
        return live[0] if len(live) == 1 else None

    def drop_session(self, session_id: str) -> None:
        """Forget every per-session record for ``session_id``.

        Used by the orphan sweep: the session's parent stack, open skill
        spans and delegation record are dropped so later hooks rebuild state
        from scratch. The calling context's ContextVar stack is only pruned
        of *that session's* spans (matched by identity); whatever the caller's
        own session pushed there stays, because the sweep usually runs from a
        different, live session's hook.
        """
        with self._lock:
            dropped = self._session_parent_stacks.pop(session_id, None) or []
            self._session_skill_spans.pop(session_id, None)
            self._subagent_registry.pop(session_id, None)
        if dropped:
            stack = _PARENT_STACK.get()
            if stack:
                stack[:] = [sp for sp in stack if not any(sp is d for d in dropped)]

    # ── Skill spans ──────────────────────────────────────────────────────

    def has_skill_span(self, session_id: str, skill: str) -> bool:
        """True when a skill span for ``skill`` is already open this session."""
        with self._lock:
            return skill in self._session_skill_spans.get(session_id, {})

    def register_skill_span(self, session_id: str, skill: str, key: str) -> None:
        """Track an open skill span so it can be closed at the turn boundary."""
        with self._lock:
            self._session_skill_spans.setdefault(session_id, {})[skill] = key

    def pop_skill_spans(self, session_id: str) -> Dict[str, str]:
        """Return and clear all open skill spans for a session (skill -> key)."""
        with self._lock:
            return self._session_skill_spans.pop(session_id, {})

    # ── Pending starts ───────────────────────────────────────────────────

    def mark_started(self, key: str, started_at: Optional[float] = None) -> None:
        """Remember when the operation tracked under ``key`` began.

        ``started_at`` is a ``time.perf_counter()`` value; it defaults to now.
        """
        with self._lock:
            self._started_at[key] = started_at if started_at is not None else time.perf_counter()

    def has_started(self, key: str) -> bool:
        """True while a start is pending under ``key``."""
        with self._lock:
            return key in self._started_at

    def pop_started(self, key: str) -> Optional[float]:
        """Forget and return the ``perf_counter`` start recorded under ``key``, or None."""
        with self._lock:
            return self._started_at.pop(key, None)

    def pop_elapsed(self, key: str) -> Optional[float]:
        """Seconds since :meth:`mark_started` was called for ``key``, forgetting
        the start; None when no start is pending (the pre-hook never fired)."""
        started = self.pop_started(key)
        return time.perf_counter() - started if started is not None else None

    def record_tool_output_chars(self, key: str, chars: int) -> None:
        with self._lock:
            self._tool_output_chars[key] = chars

    def pop_tool_output_chars(self, key: str) -> Optional[int]:
        with self._lock:
            return self._tool_output_chars.pop(key, None)

    # ── Sub-agent delegation ─────────────────────────────────────────────

    def register_subagent(
        self, child_session_id: str, record: Dict[str, Any], started_at: Optional[float] = None
    ) -> None:
        """Remember the delegation span opened for ``child_session_id``."""
        with self._lock:
            self._remember(self._subagent_registry, str(child_session_id), record)
            self._delegation_started_at[str(child_session_id)] = (
                started_at if started_at is not None else time.perf_counter()
            )

    def end_delegation(self, child_session_id: Optional[str]) -> None:
        """Forget a delegation's start time (its span has ended)."""
        if not child_session_id:
            return
        with self._lock:
            self._delegation_started_at.pop(str(child_session_id), None)

    def expired_delegations(self, ttl_seconds: float) -> list:
        """Child session ids whose delegation started more than ``ttl_seconds`` ago."""
        with self._lock:
            now = time.perf_counter()
            return [
                cid
                for cid, started in self._delegation_started_at.items()
                if now - started > ttl_seconds
            ]

    def get_subagent(self, child_session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """The delegation record for a child session, or None."""
        if not child_session_id:
            return None
        with self._lock:
            return self._subagent_registry.get(str(child_session_id))

    def pop_subagent(self, child_session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Remove and return the delegation record, or None."""
        if not child_session_id:
            return None
        with self._lock:
            return self._subagent_registry.pop(str(child_session_id), None)

    # ── Turn registry (orphan sweep) ─────────────────────────────────────

    def register_turn(self, session_id: str, started_at: Optional[float] = None) -> None:
        """Record the start time of a session's turn for TTL tracking.

        ``started_at`` is a ``time.perf_counter()``-style monotonic value
        (seconds, arbitrary epoch). Defaults to ``now``; tests pass a
        back-dated value to simulate timeouts without monkeypatching ``time``.
        """
        if not session_id:
            return
        with self._lock:
            self._turn_started_at[session_id] = (
                started_at if started_at is not None else time.perf_counter()
            )

    def unregister_turn(self, session_id: str) -> None:
        """Forget a session's turn start (normal end)."""
        with self._lock:
            self._turn_started_at.pop(session_id, None)

    def turn_started_at(self, session_id: Optional[str]) -> Optional[float]:
        """When the session's current turn started, or None."""
        if not session_id:
            return None
        with self._lock:
            return self._turn_started_at.get(session_id)

    def expired_turns(self, ttl_seconds: float) -> list:
        """Sessions whose turn started more than ``ttl_seconds`` ago."""
        with self._lock:
            if not self._turn_started_at:
                return []
            now = time.perf_counter()
            return [
                sid for sid, started in self._turn_started_at.items() if now - started > ttl_seconds
            ]

    def link_key(self, session_id: str, key: str) -> None:
        """Remember that ``key`` was opened under ``session_id``."""
        with self._lock:
            keys = self._session_keys.get(session_id)
            if keys is None:
                keys = set()
                self._remember(self._session_keys, session_id, keys)
            keys.add(key)

    def unlink_key(self, key: str) -> None:
        """Forget ``key`` in every session's active-key set."""
        with self._lock:
            for sid, keys in list(self._session_keys.items()):
                keys.discard(key)
                if not keys:
                    self._session_keys.pop(sid, None)

    def pop_session_keys(self, session_id: str) -> set:
        """Return and forget the active keys opened under ``session_id``."""
        with self._lock:
            self._turn_started_at.pop(session_id, None)
            return self._session_keys.pop(session_id, set())

    def clear_turns(self) -> None:
        with self._lock:
            self._turn_started_at.clear()
            self._session_keys.clear()

    # ── Ending spans ─────────────────────────────────────────────────────

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
        with self._lock:
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

    def end_all(self, attributes: Optional[dict] = None) -> None:
        """End every tracked span and drop all shared state (cleanup).

        Clears the session-keyed stacks, skill spans, pending starts and the
        delegation registry, plus *this* context's ContextVar stack. Other
        contexts' ContextVar stacks are unreachable from here and are left
        as they are. ``attributes`` are set on every span ended here (the
        shutdown sweep marker, #477).
        """
        with self._lock:
            keys = list(self._active_spans.keys())
        for key in keys:
            self.end_span(key, attributes=attributes)
        with self._lock:
            self._active_spans.clear()
            self._session_parent_stacks.clear()
            self._session_skill_spans.clear()
            self._started_at.clear()
            self._tool_output_chars.clear()
            self._subagent_registry.clear()
            self._delegation_started_at.clear()
        stack = _PARENT_STACK.get()
        if stack is not None:
            stack.clear()
