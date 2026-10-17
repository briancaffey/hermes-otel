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
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .helpers import truncate_string


@dataclass
class TurnSummary:
    """Per-session aggregator of per-turn telemetry, flushed onto the
    session/agent span in ``on_session_end``. The hooks mutate it through
    the intent methods below, never field by field."""

    tool_names: Set[str] = field(default_factory=set)
    # Preserves insertion order for "first N chars" joined output.
    tool_targets: List[str] = field(default_factory=list)
    tool_commands: List[str] = field(default_factory=list)
    tool_outcomes: Set[str] = field(default_factory=set)
    skill_names: Set[str] = field(default_factory=set)
    api_call_count: int = 0

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

    def add_api_call(self) -> None:
        """One more main-loop API request on this turn (``pre_api_request``)."""
        self.api_call_count += 1


# Canonical token-total field order: the usage buckets a turn accumulates.
USAGE_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)

# Cost statuses that carry a real dollar figure (#252). Hermes's other two,
# ``included`` (subscription route: an API-equivalent estimate is not a charge)
# and ``unknown`` (no price for the model), have no amount to report.
PRICED_COST_STATUSES = frozenset({"actual", "estimated"})


def _empty_usage() -> Dict[str, int]:
    return dict.fromkeys(USAGE_FIELDS, 0)


def cost_status_attributes(
    statuses: Iterable[str], usd: float, status_key: str, usage_key: str
) -> Dict[str, Any]:
    """``status_key`` always, plus ``usage_key`` only when every call was priced.

    The one pricing rule for a turn, its auxiliary calls and a delegation
    subtree: ``partial`` with no amount when only some calls were priced, so a
    subtotal is never read as the whole; empty when no call reported a status.
    Callers spell both keys out in full so the semconv drift check sees them.
    """
    seen = set(statuses)
    if not seen:
        return {}
    if seen <= PRICED_COST_STATUSES:
        status = "actual" if seen == {"actual"} else "estimated"
        return {status_key: status, usage_key: usd}
    if seen & PRICED_COST_STATUSES:
        return {status_key: "partial"}
    return {status_key: "included" if seen == {"included"} else "unknown"}


@dataclass
class PerSession:
    """All hook-scoped state buffered for a single session.

    ``usage_updated`` tracks whether any API call has reported usage, so
    ``on_session_end`` can skip emitting zero-valued token attributes
    when no LLM traffic actually occurred.

    ``io_captured`` is set by :meth:`capture_input` with the first input, so
    continuation turns don't overwrite the first user message. The hooks
    mutate a session through the intent methods below, never field by field.
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

    def capture_input(self, preview: Optional[str]) -> None:
        """Keep the session's first LLM input; later turns do not overwrite it."""
        if not self.io_captured:
            self.io["input"] = preview or ""
            self.io_captured = True

    def capture_output(self, preview: Optional[str]) -> None:
        """Keep the latest LLM output; never buffered without a captured input."""
        if self.io_captured:
            self.io["output"] = preview or ""

    def set_sender(self, sender_id: str, user_id: str) -> None:
        self.sender_id = sender_id
        self.user_id = user_id

    def note_stop(self, reason: str) -> None:
        self.stop_reason = reason

    def note_verify(self) -> None:
        self.verify_nudges += 1

    def record_attempt(
        self,
        totals: Optional[Dict[str, int]],
        cost: Optional[Dict[str, Any]],
        *,
        provider: str,
        response_model: str,
    ) -> None:
        """Account one completed main-loop API attempt.

        A success clears ``last_api_failed`` (a retry that worked, #467) even
        without usage; with usage, the totals, the real provider and the
        reported response model roll onto the turn for ``on_session_end``.
        """
        self.last_api_failed = False
        if totals is None:
            return
        for key in USAGE_FIELDS:
            self.usage[key] += totals[key]
        self.usage_updated = True
        if provider:
            self.provider = provider
        if response_model:
            self.response_model = truncate_string(response_model, 200)
        if cost is not None:
            self.add_cost(cost["status"], cost["usd"])

    def record_api_failure(self, error_type: str) -> None:
        """Remember why the last main-loop attempt failed (#467)."""
        self.last_api_failed = True
        if error_type:
            self.last_error_type = error_type

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
        attrs.update(
            cost_status_attributes(
                self.aux_cost_statuses,
                self.aux_cost_usd,
                "hermes.turn.auxiliary.cost.status",
                "hermes.turn.auxiliary.cost.usage",
            )
        )
        return attrs

    def api_failure_close(self, otherwise: str) -> Tuple[Dict[str, Any], str, Optional[str]]:
        """How finalize or the sweep closes a root that never got its
        ``on_session_end``: the ``(attributes, status, error_message)`` for
        ``end_span``.

        Hermes returns from a turn whose every main-loop API attempt failed
        without post_llm_call or on_session_end (#467), so this is the only
        place such a turn can be recorded as failed: an error naming the last
        error type. Any other turn closes OK with ``otherwise`` as its status.
        """
        if not self.last_api_failed:
            return {"hermes.turn.final_status": otherwise}, "ok", None
        attrs: Dict[str, Any] = {
            "hermes.turn.final_status": "failed",
            "hermes.session.failed": True,
        }
        if self.last_error_type:
            attrs["error.type"] = self.last_error_type
        return attrs, "error", f"turn failed: {self.last_error_type or 'API request failed'}"

    def cost_rollup(self) -> Dict[str, Any]:
        """The turn's ``hermes.cost.*`` for the root span (empty when no call was priced)."""
        return cost_status_attributes(
            self.cost_statuses, self.cost_usd, "hermes.cost.status", "hermes.cost.usage"
        )


@dataclass
class DelegationTotals:
    """What one delegated child cost, its own turns plus every delegation it
    made in turn (#390): the subtree under its ``subagent.*`` span."""

    usage: Dict[str, int] = field(default_factory=_empty_usage)
    cost_statuses: List[str] = field(default_factory=list)
    cost_usd: float = 0.0
    api_call_count: int = 0

    def _fold(
        self, usage: Dict[str, int], cost_statuses: List[str], cost_usd: float, api_calls: int
    ) -> None:
        for k, v in usage.items():
            self.usage[k] = self.usage.get(k, 0) + int(v or 0)
        self.cost_statuses.extend(cost_statuses)
        self.cost_usd += cost_usd
        self.api_call_count += api_calls

    def add_turn(self, ps: "PerSession") -> None:
        """Fold one finished turn of the child in."""
        self._fold(ps.usage, ps.cost_statuses, ps.cost_usd, ps.turn_summary.api_call_count)

    def add(self, other: "DelegationTotals") -> None:
        """Fold a grandchild's subtree in (a nested orchestrator)."""
        self._fold(other.usage, other.cost_statuses, other.cost_usd, other.api_call_count)

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
        attrs.update(
            cost_status_attributes(
                self.cost_statuses,
                self.cost_usd,
                "hermes.subagent.cost.status",
                "hermes.subagent.cost.usage",
            )
        )
        return attrs


class SessionState:
    """Per-session aggregators + a flat tool-timing registry.

    Held by :class:`HermesOTelPlugin` so test reset is just singleton
    re-creation — tests never need to reach into module globals.

    Hermes dispatches hooks across threads, so every check-then-insert here
    (``get_or_create``, ``next_turn``'s counter bookkeeping) runs under one
    re-entrant lock.
    """

    # Upper bound on remembered turn counters for sessions whose aggregator has
    # been drained. A long-lived gateway sees many session ids; the oldest are
    # evicted first, so a very old session that comes back simply restarts at 1.
    _MAX_TURN_COUNTERS = 4096

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, PerSession] = {}
        # Turn counters outlive the per-turn PerSession (which on_session_end
        # pops), so continuation turns keep counting: session_id -> last turn.
        self._turn_counters: "OrderedDict[str, int]" = OrderedDict()
        # Session-level bookkeeping for on_session_finalize / on_session_reset
        # (#29): when the session's first turn started (wall clock), the span
        # context of its most recently closed root (so the next session can
        # link to it; a kanban run's claim span is remembered the same way, so
        # the run's later transitions join its trace), and which session a new
        # one replaced.
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
        """Keep the span context of the session's (or kanban run's) latest
        closed root span. Bounded like every table here."""
        if not session_id or context is None:
            return
        with self._lock:
            self._remember(self._last_root_context, session_id, context)

    def root_context(self, session_id: str, *, forget: bool = False) -> Any:
        """The remembered root context, or None; ``forget`` drops it too."""
        with self._lock:
            if forget:
                return self._last_root_context.pop(session_id, None)
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

    # ── Bulk reset (used by tests via singleton re-creation) ─────────────

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._turn_counters.clear()
            self._first_seen.clear()
            self._last_root_context.clear()
            self._previous_session.clear()
            self.last_finalized = None
