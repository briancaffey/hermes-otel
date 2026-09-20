"""Per-session aggregators for hook callbacks.

Hooks fire independently (``pre_tool_call`` / ``post_tool_call`` /
``pre_llm_call`` / ``post_llm_call`` / ``pre_api_request`` /
``post_api_request`` / ``on_session_start`` / ``on_session_end``).
State that needs to persist across them — token totals, first input /
last output, per-turn summary — is buffered here keyed by ``session_id``
and flushed onto the top-level span during ``on_session_end``.

Previously these lived as four parallel module-level dicts in
the hooks module. Consolidating into ``SessionState`` makes reset trivial
(tests get a fresh ``SessionState`` whenever the tracer singleton is
re-created) and removes the need for tests to reach into module
internals.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class TurnSummary:
    """Per-session aggregator of per-turn telemetry.

    Flushed onto the session/agent span in ``on_session_end``. Also
    usable as a fallback on ``on_post_llm_call`` when no session hook
    is available.
    """

    tool_names: Set[str] = field(default_factory=set)
    # Preserves insertion order for "first N chars" joined output.
    tool_targets: List[str] = field(default_factory=list)
    tool_commands: List[str] = field(default_factory=list)
    tool_outcomes: Set[str] = field(default_factory=set)
    skill_names: Set[str] = field(default_factory=set)
    api_call_count: int = 0
    final_status: Optional[str] = None

    _seen_targets: Set[str] = field(default_factory=set)
    _seen_commands: Set[str] = field(default_factory=set)

    def add_tool(self, name: str) -> None:
        if name:
            self.tool_names.add(name)

    def add_target(self, target: Optional[str]) -> None:
        if target and target not in self._seen_targets:
            self._seen_targets.add(target)
            self.tool_targets.append(target)

    def add_command(self, command: Optional[str]) -> None:
        if command and command not in self._seen_commands:
            self._seen_commands.add(command)
            self.tool_commands.append(command)

    def add_outcome(self, outcome: Optional[str]) -> None:
        if outcome:
            self.tool_outcomes.add(outcome)

    def add_skill(self, skill: Optional[str]) -> None:
        if skill:
            self.skill_names.add(skill)


def _empty_usage() -> Dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
    }


@dataclass
class PerSession:
    """All hook-scoped state buffered for a single session.

    ``usage_updated`` tracks whether any API call has reported usage, so
    ``on_session_end`` can skip emitting zero-valued token attributes
    when no LLM traffic actually occurred.

    ``io_captured`` is set by ``on_pre_llm_call`` when the first input is
    captured, so continuation turns don't overwrite the first user message.
    """

    usage: Dict[str, int] = field(default_factory=_empty_usage)
    usage_updated: bool = False
    sender_id: str = ""
    user_id: str = ""
    correlation_id: str = ""
    io: Dict[str, str] = field(default_factory=lambda: {"input": "", "output": ""})
    io_captured: bool = False
    turn_summary: TurnSummary = field(default_factory=TurnSummary)
    # The most recent API error's ``error.type`` for this session, surfaced on
    # the root span at on_session_end so a failed turn shows *why* it failed.
    last_error_type: str = ""
    # The LLM provider seen on this session's API calls (e.g. "openrouter").
    # on_session_end only receives the platform (e.g. "cli"), so the real
    # provider is captured here for the agent-level GenAI metric dimensions.
    provider: str = ""
    # The last response model a provider reported on this session's API calls
    # (``response_model`` on post_api_request). Empty when none was reported;
    # never defaulted to the request model (#155).
    response_model: str = ""
    # 1-based index of the user turn this aggregator covers. Surfaced as the
    # ``hermes.turn.number`` span attribute so any backend can group or filter
    # a session's spans by conversation turn. See SessionState.next_turn.
    turn_number: int = 0


class SessionState:
    """Per-session aggregators + a flat tool-timing registry.

    Held by :class:`HermesOTelPlugin` so test reset is just singleton
    re-creation — tests never need to reach into module globals.

    Hermes dispatches hooks across threads, so every check-then-insert here
    (``get_or_create``, ``next_turn``'s counter bookkeeping) runs under one
    re-entrant lock.

    Tool timings are keyed by ``f"{tool_name}:{tool_call_id}"`` (with the
    legacy task id as fallback), not session-scoped, so they live in their own
    dict alongside the session aggregators.
    """

    # Upper bound on remembered turn counters for sessions whose aggregator has
    # been drained. A long-lived gateway sees many session ids; the oldest are
    # evicted first, so a very old session that comes back simply restarts at 1.
    _MAX_TURN_COUNTERS = 4096

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, PerSession] = {}
        self._tool_times: Dict[str, float] = {}
        # Turn counters outlive the per-turn PerSession (which on_session_end
        # pops), so continuation turns keep counting: session_id -> last turn.
        self._turn_counters: "OrderedDict[str, int]" = OrderedDict()
        # Session-level bookkeeping for on_session_finalize / on_session_reset
        # (#29): when the session's first turn started (wall clock), the span
        # context of its most recently closed root (so the next session can
        # link to it), and which session a new one replaced.
        self._first_seen: "OrderedDict[str, float]" = OrderedDict()
        self._last_root_context: "OrderedDict[str, Any]" = OrderedDict()
        self._previous_session: "OrderedDict[str, str]" = OrderedDict()
        # The session most recently finalized; a reset that names no old
        # session (the CLI) is taken to replace it.
        self.last_finalized: Optional[str] = None

    # ── Per-session aggregators ──────────────────────────────────────────

    def get_or_create(self, session_id: str) -> PerSession:
        """Return the aggregator for ``session_id``, creating an empty one if missing."""
        with self._lock:
            ps = self._sessions.get(session_id)
            if ps is None:
                ps = PerSession()
                self._sessions[session_id] = ps
            return ps

    def peek(self, session_id: str) -> Optional[PerSession]:
        """Return the aggregator if present, otherwise None (no creation)."""
        return self._sessions.get(session_id)

    def pop(self, session_id: str) -> Optional[PerSession]:
        """Remove and return the aggregator, or None if missing."""
        with self._lock:
            return self._sessions.pop(session_id, None)

    def has(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._sessions

    def active_count(self) -> int:
        """Number of sessions currently holding an aggregator."""
        with self._lock:
            return len(self._sessions)

    # ── Turn numbering ───────────────────────────────────────────────────

    def next_turn(self, session_id: str, reset: bool = False) -> int:
        """Advance and return the 1-based turn number for ``session_id``.

        Called once per user turn (from ``pre_llm_call``, which Hermes fires
        once per prompt, before the tool loop). ``reset=True`` (Hermes's
        ``is_first_turn``) restarts the count at 1 so a reused session id
        starts fresh. The counter is process-local: a session resumed in a
        new process (``hermes -r``) restarts at 1.
        """
        if not session_id:
            return 0
        with self._lock:
            current = 0 if reset else self._turn_counters.get(session_id, 0)
            turn = current + 1
            self._turn_counters[session_id] = turn
            self._turn_counters.move_to_end(session_id)
            while len(self._turn_counters) > self._MAX_TURN_COUNTERS:
                self._turn_counters.popitem(last=False)
            if reset or session_id not in self._first_seen:
                self._remember(self._first_seen, session_id, time.time())
            self.get_or_create(session_id).turn_number = turn
            return turn

    # ── Session boundaries (#29) ─────────────────────────────────────────

    def _remember(self, table: "OrderedDict[str, Any]", session_id: str, value: Any) -> None:
        table[session_id] = value
        table.move_to_end(session_id)
        while len(table) > self._MAX_TURN_COUNTERS:
            table.popitem(last=False)

    def first_seen(self, session_id: str) -> Optional[float]:
        """Wall-clock time the session's first turn started, if seen in this process."""
        with self._lock:
            return self._first_seen.get(session_id)

    def remember_root_context(self, session_id: str, context: Any) -> None:
        """Keep the span context of the session's latest closed root span."""
        if not session_id or context is None:
            return
        with self._lock:
            self._remember(self._last_root_context, session_id, context)

    def root_context(self, session_id: str) -> Any:
        with self._lock:
            return self._last_root_context.get(session_id)

    def set_previous(self, new_session_id: str, old_session_id: str) -> None:
        """Record that ``new_session_id`` replaced ``old_session_id`` (a reset)."""
        if not new_session_id or not old_session_id or new_session_id == old_session_id:
            return
        with self._lock:
            self._remember(self._previous_session, new_session_id, old_session_id)

    def previous_session(self, session_id: str) -> Optional[str]:
        with self._lock:
            return self._previous_session.get(session_id)

    def drop_session(self, session_id: str) -> Dict[str, Any]:
        """Forget everything about ``session_id`` and return what was known.

        Called at the session's true end (finalize / reset). The turn counter
        goes too, so a session id that comes back starts at turn 1. The root
        context is kept so a later session can still link to it.
        """
        with self._lock:
            ps = self._sessions.pop(session_id, None)
            turns = self._turn_counters.pop(session_id, 0)
            if ps is not None and ps.turn_number:
                turns = max(turns, ps.turn_number)
            first_seen = self._first_seen.pop(session_id, None)
            self._previous_session.pop(session_id, None)
            return {"turns": turns, "first_seen": first_seen, "had_state": ps is not None}

    def turn_number(self, session_id: str) -> int:
        """Current turn number for ``session_id`` (0 = no turn started yet)."""
        if not session_id:
            return 0
        with self._lock:
            ps = self._sessions.get(session_id)
            if ps is not None and ps.turn_number:
                return ps.turn_number
            return self._turn_counters.get(session_id, 0)

    # ── Tool timings ─────────────────────────────────────────────────────

    def record_tool_start(self, key: str, started_at: float) -> None:
        with self._lock:
            self._tool_times[key] = started_at

    def pop_tool_start(self, key: str) -> Optional[float]:
        with self._lock:
            return self._tool_times.pop(key, None)

    def has_tool_start(self, key: str) -> bool:
        with self._lock:
            return key in self._tool_times

    # ── Bulk reset (used by tests via singleton re-creation) ─────────────

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._tool_times.clear()
            self._turn_counters.clear()
            self._first_seen.clear()
            self._last_root_context.clear()
            self._previous_session.clear()
            self.last_finalized = None
