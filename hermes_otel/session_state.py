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
    # True while this turn's most recent main-loop API attempt ended in
    # api_request_error with no successful post_api_request after it. Hermes
    # fires neither post_llm_call nor on_session_end when every attempt fails
    # (#467), so finalize and the orphan sweep read this to close the root as
    # failed instead of finalized / timed_out.
    last_api_failed: bool = False
    # The LLM provider seen on this session's API calls (e.g. "openrouter").
    # on_session_end only receives the platform (e.g. "cli"), so the real
    # provider is captured here for the agent-level GenAI metric dimensions.
    provider: str = ""
    # The last response model a provider reported on this session's API calls
    # (``response_model`` on post_api_request). Empty when none was reported;
    # never defaulted to the request model (#155).
    response_model: str = ""
    # The model the turn started on (``on_session_start`` / the lazy root),
    # kept so a delegated child can say whether it inherited it (#391).
    model: str = ""
    # agent_loop_stopped (#396): the reason the gateway gave when it stopped
    # this turn, so the root ends "interrupted" even if only the sweep ends it.
    stop_reason: str = ""
    # pre_verify fired this many times this turn (#396).
    verify_nudges: int = 0
    # 1-based index of the user turn this aggregator covers. Surfaced as the
    # ``hermes.turn.number`` span attribute so any backend can group or filter
    # a session's spans by conversation turn. See SessionState.next_turn.
    turn_number: int = 0
    # Per-call cost statuses from Hermes's pricing (#252) and the sum of the
    # priced calls' USD. The root carries a total only when every call was
    # priced; see cost_rollup.
    cost_statuses: List[str] = field(default_factory=list)
    cost_usd: float = 0.0
    # True when this session is a delegated child whose root rejoined the
    # parent's delegation span; its API calls report ``query_source=subagent``.
    is_subagent: bool = False
    # Auxiliary LLM calls (#389): titling, compression, MoA references and
    # aggregator, vision, approval, the goal judge ... run under this turn.
    # Kept apart from ``usage`` / ``cost_usd``: the main loop's ``api.*`` span
    # for the ``moa`` provider already carries what the MoA facade returns, so
    # folding the auxiliary attempts into the turn totals would count them
    # twice. They are surfaced as ``hermes.turn.auxiliary.*`` on the root.
    aux_usage: Dict[str, int] = field(default_factory=_empty_usage)
    aux_call_count: int = 0
    aux_error_count: int = 0
    aux_cost_statuses: List[str] = field(default_factory=list)
    aux_cost_usd: float = 0.0

    def add_cost(self, status: str, usd: Optional[float]) -> None:
        self.cost_statuses.append(status)
        if usd is not None:
            self.cost_usd += usd

    def add_auxiliary(
        self, totals: Optional[Dict[str, int]], cost: Optional[Dict[str, Any]], *, failed: bool
    ) -> None:
        """Account one auxiliary provider attempt on this turn."""
        self.aux_call_count += 1
        if failed:
            self.aux_error_count += 1
        if totals:
            for key in self.aux_usage:
                self.aux_usage[key] += int(totals.get(key, 0) or 0)
        if cost:
            self.aux_cost_statuses.append(str(cost.get("status") or "unknown"))
            usd = cost.get("usd")
            if usd is not None:
                self.aux_cost_usd += float(usd)

    def auxiliary_rollup(self) -> Dict[str, Any]:
        """``hermes.turn.auxiliary.*`` for the root span (empty when none ran).

        Same pricing rule as :meth:`cost_rollup`: a USD total only when every
        auxiliary call was priced, ``partial`` otherwise.
        """
        if not self.aux_call_count:
            return {}
        attrs: Dict[str, Any] = {"hermes.turn.auxiliary.api_call_count": self.aux_call_count}
        if self.aux_error_count:
            attrs["hermes.turn.auxiliary.error_count"] = self.aux_error_count
        if self.aux_usage["prompt_tokens"] or self.aux_usage["completion_tokens"]:
            attrs["hermes.turn.auxiliary.input_tokens"] = self.aux_usage["prompt_tokens"]
            attrs["hermes.turn.auxiliary.output_tokens"] = self.aux_usage["completion_tokens"]
        statuses = set(self.aux_cost_statuses)
        if statuses and statuses <= {"actual", "estimated"}:
            attrs["hermes.turn.auxiliary.cost.status"] = (
                "actual" if statuses == {"actual"} else "estimated"
            )
            attrs["hermes.turn.auxiliary.cost.usage"] = self.aux_cost_usd
        elif statuses & {"actual", "estimated"}:
            attrs["hermes.turn.auxiliary.cost.status"] = "partial"
        elif statuses:
            attrs["hermes.turn.auxiliary.cost.status"] = (
                "included" if statuses == {"included"} else "unknown"
            )
        return attrs

    def api_failure_close(self) -> Optional[Dict[str, Any]]:
        """Root attributes for a turn closed by finalize or the sweep whose
        last main-loop API attempt failed (#467), or None when it did not.

        Hermes returns from that path without post_llm_call or on_session_end,
        so this is the only place the turn can be recorded as failed.
        """
        if not self.last_api_failed:
            return None
        attrs: Dict[str, Any] = {
            "hermes.turn.final_status": "failed",
            "hermes.session.failed": True,
        }
        if self.last_error_type:
            attrs["error.type"] = self.last_error_type
        return attrs

    def cost_rollup(self) -> Dict[str, Any]:
        """The turn's cost attributes for the root span (empty when no call was priced).

        ``hermes.cost.usage`` is set only when every API call had a price;
        a turn mixing priced and unpriced calls reports ``partial`` with no
        amount, so a subtotal is never read as the turn's cost.
        """
        statuses = set(self.cost_statuses)
        if not statuses:
            return {}
        if statuses <= {"actual", "estimated"}:
            status = "actual" if statuses == {"actual"} else "estimated"
            return {"hermes.cost.status": status, "hermes.cost.usage": self.cost_usd}
        if statuses & {"actual", "estimated"}:
            return {"hermes.cost.status": "partial"}
        return {"hermes.cost.status": "included" if statuses == {"included"} else "unknown"}


@dataclass
class DelegationTotals:
    """What one delegated child cost, its own turns plus every delegation it
    made in turn (#390): the subtree under its ``subagent.*`` span."""

    usage: Dict[str, int] = field(default_factory=_empty_usage)
    cost_statuses: List[str] = field(default_factory=list)
    cost_usd: float = 0.0
    api_call_count: int = 0

    def add_turn(self, ps: "PerSession") -> None:
        """Fold one finished turn of the child in."""
        for k, v in ps.usage.items():
            self.usage[k] = self.usage.get(k, 0) + int(v or 0)
        self.cost_statuses.extend(ps.cost_statuses)
        self.cost_usd += ps.cost_usd
        self.api_call_count += ps.turn_summary.api_call_count

    def add(self, other: "DelegationTotals") -> None:
        """Fold a grandchild's subtree in (a nested orchestrator)."""
        for k, v in other.usage.items():
            self.usage[k] = self.usage.get(k, 0) + int(v or 0)
        self.cost_statuses.extend(other.cost_statuses)
        self.cost_usd += other.cost_usd
        self.api_call_count += other.api_call_count

    def attributes(self) -> Dict[str, Any]:
        """``hermes.subagent.*`` rollup attributes for the delegation span.

        Namespaced, not ``gen_ai.usage.*``: the child's own root already
        carries its turn's ``gen_ai.usage.*``, and a reader that sums those
        over every agent span would count the child twice. Cost follows the
        root's rule: an amount only when every call was priced.
        """
        u = self.usage
        attrs: Dict[str, Any] = {
            "hermes.subagent.usage.input_tokens": u.get("prompt_tokens", 0),
            "hermes.subagent.usage.output_tokens": u.get("completion_tokens", 0),
            "hermes.subagent.usage.total_tokens": u.get("total_tokens", 0),
            "hermes.subagent.api_call_count": self.api_call_count,
        }
        statuses = set(self.cost_statuses)
        if statuses and statuses <= {"actual", "estimated"}:
            attrs["hermes.subagent.cost.status"] = (
                "actual" if statuses == {"actual"} else "estimated"
            )
            attrs["hermes.subagent.cost.usage"] = self.cost_usd
        elif statuses & {"actual", "estimated"}:
            attrs["hermes.subagent.cost.status"] = "partial"
        elif statuses:
            attrs["hermes.subagent.cost.status"] = (
                "included" if statuses == {"included"} else "unknown"
            )
        return attrs


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
        # Raw terminal output size per tool key, recorded by the
        # transform_terminal_output observer and read back in post_tool_call.
        self._tool_output_chars: Dict[str, int] = {}
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
        # Delegated children's running totals, child_session_id -> totals,
        # until subagent_stop reads them onto the delegation span (#390).
        self._delegations: Dict[str, DelegationTotals] = {}
        # Gateway session key -> the session id of its latest turn, so
        # agent_loop_stopped (which names only the key) finds the turn (#396).
        self._session_keys: "OrderedDict[str, str]" = OrderedDict()
        # Session ids whose root span this process has already counted in
        # ``hermes.session.count`` (#473): outlives the per-turn PerSession so
        # continuation turns (synthesised roots) count nothing.
        self._counted: "OrderedDict[str, bool]" = OrderedDict()

    def remember_session_key(self, session_key: str, session_id: str) -> None:
        with self._lock:
            self._remember(self._session_keys, session_key, session_id)

    def session_for_key(self, session_key: Optional[str]) -> Optional[str]:
        if not session_key:
            return None
        with self._lock:
            return self._session_keys.get(session_key)

    # ── Delegation rollups (#390) ────────────────────────────────────────

    def add_delegation_turn(self, child_session_id: str, ps: "PerSession") -> None:
        with self._lock:
            self._delegations.setdefault(child_session_id, DelegationTotals()).add_turn(ps)

    def add_delegation_subtree(self, child_session_id: str, totals: DelegationTotals) -> None:
        with self._lock:
            self._delegations.setdefault(child_session_id, DelegationTotals()).add(totals)

    def pop_delegation(self, child_session_id: Optional[str]) -> Optional[DelegationTotals]:
        if not child_session_id:
            return None
        with self._lock:
            return self._delegations.pop(child_session_id, None)

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

    def next_turn(self, session_id: str, reset: bool = False, history_turns: int = 0) -> int:
        """Advance and return the 1-based turn number for ``session_id``.

        Called once per user turn (from ``pre_llm_call``, which Hermes fires
        once per prompt, before the tool loop). ``reset=True`` (Hermes's
        ``is_first_turn``) restarts the count at 1 so a reused session id
        starts fresh. The counter is process-local, so ``history_turns`` (the
        user prompts in the turn's conversation, current one included) is a
        floor: a session resumed in a new process (``hermes -r``) continues
        its numbering instead of restarting at 1.
        """
        if not session_id:
            return 0
        with self._lock:
            current = 0 if reset else self._turn_counters.get(session_id, 0)
            turn = 1 if reset else max(current + 1, int(history_turns or 0))
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

    def first_root(self, session_id: str) -> bool:
        """True once per session id per process: the caller counts the session.

        A real ``on_session_start`` and a root synthesised on ``pre_llm_call``
        (resumed, gateway and cron turns, where Hermes never fires
        ``on_session_start`` in this process) both create a root; whichever
        comes first wins and every later root of the same session is a
        continuation turn (#473). ``drop_session`` forgets the id, so a
        session that is finalized and then reused counts again.
        """
        if not session_id:
            return False
        with self._lock:
            if session_id in self._counted:
                return False
            self._remember(self._counted, session_id, True)
            return True

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
            self._counted.pop(session_id, None)
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

    def record_tool_output_chars(self, key: str, chars: int) -> None:
        with self._lock:
            self._tool_output_chars[key] = chars

    def pop_tool_output_chars(self, key: str) -> Optional[int]:
        with self._lock:
            return self._tool_output_chars.pop(key, None)

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
