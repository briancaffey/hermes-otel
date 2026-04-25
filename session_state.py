"""Per-session aggregators for hook callbacks.

Hooks fire independently (``pre_tool_call`` / ``post_tool_call`` /
``pre_llm_call`` / ``post_llm_call`` / ``pre_api_request`` /
``post_api_request`` / ``on_session_start`` / ``on_session_end``).
State that needs to persist across them — token totals, first input /
last output, per-turn summary — is buffered here keyed by ``session_id``
and flushed onto the top-level span during ``on_session_end``.

Previously these lived as four parallel module-level dicts in
``hooks.py``. Consolidating into ``SessionState`` makes reset trivial
(tests get a fresh ``SessionState`` whenever the tracer singleton is
re-created) and removes the need for tests to reach into module
internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


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
    }


@dataclass
class PerSession:
    """All hook-scoped state buffered for a single session.

    ``usage_updated`` tracks whether any API call has reported usage, so
    ``on_session_end`` can skip emitting zero-valued token attributes
    when no LLM traffic actually occurred.

    ``io_captured`` mirrors the old ``session_id in _SESSION_IO``
    behaviour — on_pre_llm_call sets it when the first input is captured,
    so continuation turns don't overwrite the first user message.
    """

    usage: Dict[str, int] = field(default_factory=_empty_usage)
    usage_updated: bool = False
    sender_id: str = ""
    io: Dict[str, str] = field(default_factory=lambda: {"input": "", "output": ""})
    io_captured: bool = False
    turn_summary: TurnSummary = field(default_factory=TurnSummary)


class SessionState:
    """Per-session aggregators + a flat tool-timing registry.

    Held by :class:`HermesOTelPlugin` so test reset is just singleton
    re-creation — tests never need to reach into module globals.

    Tool timings are keyed by ``f"{tool_name}:{task_id}"`` (task-scoped,
    not session-scoped) so they live in their own dict alongside the
    session aggregators.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, PerSession] = {}
        self._tool_times: Dict[str, float] = {}

    # ── Per-session aggregators ──────────────────────────────────────────

    def get_or_create(self, session_id: str) -> PerSession:
        """Return the aggregator for ``session_id``, creating an empty one if missing."""
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
        return self._sessions.pop(session_id, None)

    def has(self, session_id: str) -> bool:
        return session_id in self._sessions

    # ── Tool timings ─────────────────────────────────────────────────────

    def record_tool_start(self, key: str, started_at: float) -> None:
        self._tool_times[key] = started_at

    def pop_tool_start(self, key: str) -> Optional[float]:
        return self._tool_times.pop(key, None)

    def has_tool_start(self, key: str) -> bool:
        return key in self._tool_times

    # ── Bulk reset (used by tests via singleton re-creation) ─────────────

    def clear(self) -> None:
        self._sessions.clear()
        self._tool_times.clear()
