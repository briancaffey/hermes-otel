"""Session hooks: the turn root (``agent`` / ``cron`` span) and its summary."""

from __future__ import annotations

import re
import time
from typing import Any, Dict, Optional

from .. import log_events as EV
from ..debug_utils import debug_log, logger
from ..helpers import truncate_string
from ..instruments import COMPAT_SESSION_LABEL
from ._common import _emit_event, _fail_open, get_tracer
from .attributes import (
    _extract_correlation_id,
    _gen_ai_attributes,
    _model_attributes,
    _per_session_sender_attributes,
    _platform_attributes,
    _response_model_attributes,
    _session_identity_attributes,
    _summary_attributes,
    _weave_turn_attributes,
    correlation_attributes,
    model_override_attributes,
    profile_attributes,
    session_env,
    turn_trigger_attributes,
)
from .kanban import kanban_remote_parent
from .propagation import remote_parent
from .usage import _record_genai_token_usage, genai_metric_dims, usage_attributes

try:
    from opentelemetry.trace import Link
except ImportError:  # pragma: no cover — plugin is unusable without OTel
    Link = None  # type: ignore[assignment]


def detect_session_kind(platform: Any, kwargs: Dict[str, Any]) -> str:
    """Classify a run as ``session``, ``cron`` or a host-supplied kind.

    First non-empty of ``session_type`` / ``origin`` / ``run_type`` wins;
    otherwise ``cron`` when the platform, source or trigger mentions cron or a
    cron job id is present; else ``session``.
    """
    for key in ("session_type", "origin", "run_type"):
        value = kwargs.get(key)
        if value:
            return str(value)
    for candidate in (platform, kwargs.get("source"), kwargs.get("trigger")):
        if candidate and "cron" in str(candidate).lower():
            return "cron"
    if kwargs.get("job_id") or kwargs.get("cron_job_id"):
        return "cron"
    return "session"


# Hermes's scheduler names a cron run's session ``cron_<job id>_<YYYYmmdd>_<HHMMSS>``
# (cron/scheduler.py) and passes no job id on on_session_start.
_CRON_SESSION_RE = re.compile(r"^cron_(?P<job>.+)_\d{8}_\d{6}$")


def cron_job_id_from_session(session_id: Any) -> str:
    """The cron job id embedded in a cron run's session id, or ``""``."""
    m = _CRON_SESSION_RE.match(str(session_id or ""))
    return m.group("job") if m else ""


def _annotate_delegation_model(span: Any, model: Any, parent_model: Any) -> None:
    text = truncate_string(model, 200) if model else ""
    if not text:
        return
    try:
        span.set_attribute("gen_ai.request.model", text)
        if parent_model:
            span.set_attribute("hermes.subagent.model_inherited", text == str(parent_model))
    except Exception:
        pass


def _start_session_span(
    session_id: str,
    model: str,
    platform: str,
    extra_kwargs: dict,
    *,
    synthesized: bool,
) -> None:
    """Create + push the top-level session/agent/cron span.

    Shared between ``on_session_start`` (first turn of a session) and
    ``on_pre_llm_call`` (lazy fallback for continuation turns, since
    hermes fires on_session_start only on turn 1 but on_session_end
    fires per turn). When ``synthesized=True`` we tag the span so the
    origin is visible in the backend UI.
    """
    tracer = get_tracer()
    kind = detect_session_kind(platform, extra_kwargs)
    span_name = "agent" if kind != "cron" else "cron"
    key = f"session:{session_id}"

    # The root span is where a session's aggregator is born; pin the
    # host-supplied correlation id (if any) so every later span reuses it.
    ps = tracer.sessions.get_or_create(session_id)
    ps.model = str(model or "")
    gateway_key = session_env("HERMES_SESSION_KEY").strip()
    if gateway_key:
        tracer.sessions.remember_session_key(gateway_key, session_id)
    if not ps.correlation_id:
        ps.correlation_id = _extract_correlation_id(extra_kwargs)
    # Count the session when this process first roots it, whichever hook
    # gets there first (#473): a resumed, gateway or cron session has no
    # on_session_start in this process and used to go uncounted.
    if tracer.sessions.first_root(session_id):
        tracer.record_metric("session_count", 1, {"platform": platform or "unknown"})
        # Claude Code's twin (#485): its ``start_type`` is ``fresh`` for a new
        # session and ``resume`` for one picked up again; a root this process
        # had to synthesise is a session it did not start.
        tracer.record_metric(
            "claude_code.session.count",
            1,
            {"start_type": "resume" if synthesized else "fresh", COMPAT_SESSION_LABEL: session_id},
        )

    attributes: Dict[str, Any] = {"hermes.session.kind": kind}
    attributes.update(_session_identity_attributes(session_id, root=True))
    attributes.update(_platform_attributes(platform))
    attributes.update(profile_attributes(tracer))
    # Hermes passes no provider on on_session_start; a host that does is honoured.
    # Otherwise the provider lands on the root at on_session_end, once the
    # turn's API calls have reported it (#153).
    attributes.update(_model_attributes(model, extra_kwargs.get("provider")))
    attributes.update(model_override_attributes(model))
    attributes.update(_gen_ai_attributes(session_id, "invoke_agent"))
    attributes.update(
        _weave_turn_attributes(session_id, extra_kwargs, getattr(tracer, "profile_name", None))
    )
    attributes.update(correlation_attributes(tracer, session_id, extra_kwargs))
    if synthesized:
        attributes["hermes.session.synthesized"] = True

    cron_job_id = (
        extra_kwargs.get("job_id")
        or extra_kwargs.get("cron_job_id")
        or cron_job_id_from_session(session_id)
    )
    if cron_job_id:
        attributes["hermes.cron.job_id"] = truncate_string(cron_job_id, 200)

    # Sub-agent rejoin: if this session is a delegated child (its session_id was
    # registered by on_subagent_start in the parent), nest its root span under
    # the delegation span so the whole multi-agent run is one connected trace.
    # In-process delegation has the live span → real parent. Cross-process only
    # has the SpanContext → attach a link instead (best-effort correlation).
    parent_override = None
    links = None
    record = tracer.spans.get_subagent(session_id)
    if record:
        attributes["hermes.session.is_subagent"] = True
        ps.is_subagent = True
        if record.get("role"):
            attributes["hermes.subagent.role"] = truncate_string(record["role"], 200)
        if record.get("parent_session_id"):
            attributes["hermes.subagent.parent_session_id"] = truncate_string(
                record["parent_session_id"], 200
            )
        span_obj = record.get("span")
        if span_obj is not None and hasattr(span_obj, "get_span_context"):
            parent_override = span_obj
            # subagent_start does not carry the child's model; its root does.
            # Put it on the delegation span so the agent-level span says which
            # model the child ran on and whether it is the parent's (#391).
            _annotate_delegation_model(span_obj, model, record.get("parent_model"))
        elif record.get("context") is not None and Link is not None:
            links = [Link(record["context"])]

    attributes.update(turn_trigger_attributes(platform, is_subagent=bool(record)))

    # Kanban worker (#388): the dispatcher put its claim span in TRACEPARENT
    # before spawning this process, so the run is one trace.
    if parent_override is None and not links:
        parent_override = kanban_remote_parent()
    # Any other launcher (#474): HERMES_OTEL_TRACEPARENT names the outer span.
    if parent_override is None and not links and tracer.config.remote_parent:
        parent_override = remote_parent()
        if parent_override is not None:
            attributes["hermes.trace.remote_parent"] = True

    # Session chaining (#29): a session that replaced another (``/new``,
    # ``/reset``, expiry) names it and links to its last root span.
    previous = tracer.sessions.previous_session(session_id)
    if previous:
        attributes["hermes.session.previous_id"] = truncate_string(previous, 200)
        prev_ctx = tracer.sessions.root_context(previous)
        if prev_ctx is not None and Link is not None:
            links = list(links or []) + [
                Link(prev_ctx, attributes={"hermes.link": "previous_session"})
            ]

    span = tracer.start_span(
        name=span_name,
        key=key,
        kind="agent",
        attributes=attributes,
        session_id=session_id,
        parent=parent_override,
        links=links,
    )
    tracer.spans.push_parent(span, session_id=session_id)
    tracer.register_turn(session_id)
    debug_log(f"  session span started: key={key}, name={span_name}, synthesized={synthesized}")


@_fail_open
def on_session_start(session_id: str, model: str, platform: str, **kwargs):
    """Start a top-level session span (or cron span) for the entire run."""
    tracer = get_tracer()
    debug_log(f"on_session_start fired: session={session_id}, platform={platform}")
    if not tracer.is_enabled:
        return

    tracer.sweep_expired_turns()
    if session_id:
        # A previous turn of this session that failed and never ended (#467).
        tracer.close_failed_turn(session_id)
    _start_session_span(session_id, model, platform, kwargs, synthesized=False)


@_fail_open
def on_session_end(
    session_id: str, completed: bool, interrupted: bool, model: str, platform: str, **kwargs
):
    """Close the top-level session span."""
    tracer = get_tracer()
    debug_log(
        f"on_session_end fired: session={session_id}, completed={completed}, interrupted={interrupted}"
    )
    if not tracer.is_enabled:
        return

    key = f"session:{session_id}"
    # Hermes reports these on the same hook; the plugin used to ignore them and
    # compute its own status (#156).
    failed = bool(kwargs.get("failed"))
    exit_reason = kwargs.get("turn_exit_reason")

    # Drain the aggregators in one shot. Everything this session buffered
    # — I/O, usage totals, turn summary — comes back in a single PerSession.
    ps = tracer.sessions.pop(session_id)

    attributes: Dict[str, Any] = {
        "hermes.session.completed": bool(completed),
        "hermes.session.interrupted": bool(interrupted),
        "hermes.session.failed": failed,
    }
    if exit_reason:
        attributes["hermes.turn.exit_reason"] = truncate_string(exit_reason, 120)
    attributes.update(_platform_attributes(platform))
    attributes.update(profile_attributes(tracer))
    # Provider: what this turn's API calls reported, else what the host passed.
    provider = (ps.provider if ps is not None else "") or kwargs.get("provider")
    attributes.update(_model_attributes(model, provider))
    attributes.update(_response_model_attributes(ps.response_model if ps is not None else ""))
    attributes.update(_gen_ai_attributes(session_id, "invoke_agent"))
    attributes.update(
        _weave_turn_attributes(session_id, kwargs, getattr(tracer, "profile_name", None))
    )
    attributes.update(correlation_attributes(tracer, session_id, kwargs))

    if ps is not None and ps.io_captured:
        if ps.io.get("input"):
            attributes["input.value"] = ps.io["input"]
        if ps.io.get("output"):
            attributes["output.value"] = ps.io["output"]

    if ps is not None and ps.usage_updated:
        attributes.update(usage_attributes(ps.usage))
        # OTel GenAI agent-level token-usage histogram (per-turn/session rollup).
        # Prefer the provider captured from this session's API calls over the
        # platform, so the dimension matches the gen_ai.client.* metrics.
        agent_provider = ps.provider or kwargs.get("provider") or platform
        _record_genai_token_usage(
            tracer,
            "gen_ai.agent.token.usage",
            ps.usage,
            genai_metric_dims(model, agent_provider, model, operation="invoke_agent"),
        )

    if ps is not None and ps.is_subagent:
        # A delegated child's turn counts toward its delegation span (#390).
        tracer.sessions.add_delegation_turn(session_id, ps)

    if ps is not None:
        attributes.update(ps.cost_rollup())
        # Auxiliary calls (#389) are reported beside, never inside, the turn
        # totals: see PerSession.add_auxiliary for why.
        attributes.update(ps.auxiliary_rollup())

    # Surface the last API error's type on the root so a failed turn shows why.
    if ps is not None and ps.last_error_type:
        attributes["error.type"] = ps.last_error_type

    attributes.update(_per_session_sender_attributes(ps))
    if ps is not None and ps.turn_number:
        attributes["hermes.turn.number"] = ps.turn_number
    if ps is not None and ps.verify_nudges:
        attributes["hermes.turn.verify_nudges"] = ps.verify_nudges
    if ps is not None and ps.stop_reason:
        attributes["hermes.turn.stop_reason"] = ps.stop_reason

    if failed:
        final_status = "failed"
    elif completed:
        final_status = "completed"
    elif interrupted:
        final_status = "interrupted"
    else:
        final_status = "incomplete"
    if ps is not None:
        summary = ps.turn_summary
        if summary.final_status is None:
            summary.final_status = final_status
        attributes.update(_summary_attributes(summary))
    else:
        attributes["hermes.turn.final_status"] = final_status

    # Only failures are ERROR: a reported failure, or a turn that neither
    # completed nor was interrupted. Interruptions are user actions.
    status = "error" if failed or not (completed or interrupted) else "ok"

    # Close any skill execution-window spans opened this turn. Done before the
    # root is popped/ended so they close as children of the still-open root.
    # Overlapping skills each get their own close. Status is OK — a skill being
    # active is never itself an error; the turn outcome rides on result_status.
    if tracer.config.skill_spans:
        for _skill_name, skill_key in tracer.spans.pop_skill_spans(session_id).items():
            tracer.end_span(
                skill_key,
                attributes={"hermes.skill.result_status": final_status},
                status="ok",
            )

    root_span = tracer.spans.get_span(key)
    started_at = tracer.spans.turn_started_at(session_id)
    final = attributes.get("hermes.turn.final_status") or "incomplete"
    if isinstance(started_at, (int, float)):
        # The turn's wall-clock is the agent loop's active time: Claude
        # Code's ``active_time.total{type=cli}``; ``user`` (typing time) has
        # no Hermes signal and is never recorded (#485).
        active_s = max(0.0, time.perf_counter() - started_at)
        tracer.record_metric(
            "active_time", active_s, {"type": "cli", "platform": platform or "unknown"}
        )
        tracer.record_metric(
            "claude_code.active_time.total",
            active_s,
            {"type": "cli", COMPAT_SESSION_LABEL: session_id},
        )
    _emit_event(
        tracer,
        EV.TURN_END,
        f"turn {attributes.get('hermes.turn.number') or '?'} {final}",
        {
            **{
                k: v
                for k, v in attributes.items()
                if k.startswith(EV.TURN_END_PREFIXES) or k in EV.TURN_END_KEYS
            },
            EV.ATTR_TURN_DURATION_S: (
                round(time.perf_counter() - started_at, 3) if started_at is not None else None
            ),
        },
        severity="ERROR" if final == "failed" else ("WARN" if final == "interrupted" else "INFO"),
        session_id=session_id,
        span=root_span,
        content=False,
    )
    # Children the turn left open (a tool without post_tool_call, an api.*
    # cut mid-stream, an approval that timed out) end here, marked, before
    # the root so the root still contains them (#477).
    tracer.end_leftover_children(session_id, "turn_end")
    _remember_root(tracer, session_id)
    tracer.spans.pop_parent(session_id=session_id)
    tracer.end_span(key, attributes=attributes, status=status)
    tracer.unregister_turn(session_id)

    # End of a user-visible unit of work. Flush so the trace is visible in
    # the backend UI immediately rather than after schedule_delay_millis.
    # Honors config.force_flush_on_session_end for users who'd rather let
    # the batcher do its thing even at turn boundaries.
    if tracer.config.force_flush_on_session_end:
        # From a background thread, waited on for at most force_flush_wait_ms:
        # a healthy collector gets the turn before the hook returns (a one-shot
        # run may exit right after), a stuck one costs a bounded wait (#91).
        tracer.flush_async()
        wait_ms = tracer.config.force_flush_wait_ms
        if wait_ms and wait_ms > 0:
            tracer.flush_wait(timeout_s=wait_ms / 1000.0)

    debug_log(f"  session span ended: key={key}, status={status}")


# ── Session boundaries: on_session_finalize / on_session_reset (#29) ─────
#
# Hermes fires on_session_end after every turn and on_session_finalize once,
# at the session's true end (CLI exit, gateway expiry, /new, /reset);
# on_session_reset announces the session that takes over. A session is not
# modelled as one long-lived span (backends export a span only when it ends,
# and a gateway session can live for hours); each turn stays its own trace,
# grouped by session.id / gen_ai.conversation.id. These hooks close whatever
# is still open, record the session's size and length, drop the per-session
# state deterministically, and chain the replacing session to the old one.


def _remember_root(tracer, session_id: str) -> None:
    """Keep the root span's context so a replacing session can link to it."""
    try:
        root = tracer.spans.get_session_root(session_id)
        if root is not None and hasattr(root, "get_span_context"):
            tracer.sessions.remember_root_context(session_id, root.get_span_context())
    except Exception:
        pass


_FINAL_STATUS = {"finalize": "finalized", "reset": "reset"}
_REASON_KEY = {"finalize": "hermes.session.finalize_reason", "reset": "hermes.session.reset_reason"}


def _finalize_session(tracer, session_id: str, platform: str, reason: str, *, event: str) -> None:
    """Close an open root, emit the session summary, drop the session's state."""
    key = f"session:{session_id}"
    closed_root = tracer.spans.has_span(key)
    if closed_root:
        tracer.end_leftover_children(session_id, "session_end")
        # A turn still in flight (or an orphan) at the session's end: close it
        # as finalized rather than leaving it to the TTL sweep.
        if tracer.config.skill_spans:
            for _name, skill_key in tracer.spans.pop_skill_spans(session_id).items():
                tracer.end_span(
                    skill_key, attributes={"hermes.skill.result_status": event}, status="ok"
                )
        attributes = {
            _REASON_KEY[event]: truncate_string(reason, 120),
            "hermes.turn.final_status": _FINAL_STATUS[event],
        }
        status, error_message = "ok", None
        # Every API attempt of this turn failed and Hermes skipped
        # on_session_end (#467): the turn failed, it was not just finalized.
        ps = tracer.sessions.peek(session_id)
        failure = ps.api_failure_close() if ps is not None else None
        if failure:
            attributes.update(failure)
            status = "error"
            error_message = f"turn failed: {failure.get('error.type') or 'API request failed'}"
        _remember_root(tracer, session_id)
        tracer.spans.pop_parent(session_id=session_id)
        tracer.end_span(key, attributes=attributes, status=status, error_message=error_message)

    known = tracer.sessions.drop_session(session_id)
    tracer.spans.drop_session(session_id)
    tracer.unregister_turn(session_id)
    tracer.sessions.last_finalized = session_id

    turns = int(known.get("turns") or 0)
    first_seen = known.get("first_seen")
    duration_s = max(0.0, time.time() - first_seen) if first_seen else None
    if not (turns or first_seen or known.get("had_state") or closed_root):
        # A session this process never saw a turn of (a gateway expiry sweep
        # after a restart): nothing to summarise, nothing to log.
        debug_log(f"  session {session_id} unknown here; nothing to finalize")
        return
    if turns or first_seen:
        labels = {"platform": platform or "unknown", "reason": truncate_string(reason, 60)}
        tracer.record_metric("session_turns", turns, labels)
        if duration_s is not None:
            tracer.record_metric("session_duration", duration_s, labels)
    logger.info(
        "[hermes-otel] session %s %s (%s): %d turn(s)%s",
        session_id,
        _FINAL_STATUS[event],
        reason,
        turns,
        f" over {duration_s:.1f}s" if duration_s is not None else "",
        extra={
            "hermes.session_id": session_id,
            "hermes.session.turn_count": turns,
            "hermes.session.duration_s": duration_s if duration_s is not None else 0.0,
            _REASON_KEY[event]: reason,
        },
    )
    _emit_event(
        tracer,
        EV.SESSION_FINALIZE,
        f"session {session_id} {_FINAL_STATUS[event]} ({reason}): {turns} turn(s)"
        + (f" over {duration_s:.1f}s" if duration_s is not None else ""),
        {
            "hermes.session.turn_count": turns,
            "hermes.session.duration_s": round(duration_s, 3) if duration_s is not None else None,
            _REASON_KEY[event]: truncate_string(reason, 120),
            "hermes.platform": platform or None,
        },
        session_id=session_id,
        content=False,
    )
    # The authoritative flush: nothing of this session should wait for the
    # batcher after its end. Background, like the turn-end flush; process
    # exit still flushes synchronously through atexit.
    tracer.flush_async()
    wait_ms = tracer.config.force_flush_wait_ms
    if wait_ms and wait_ms > 0:
        tracer.flush_wait(timeout_s=wait_ms / 1000.0)


@_fail_open
def on_session_finalize(
    session_id: Optional[str] = None, platform: str = "", reason: str = "", **kwargs
):
    """The session's true end (CLI exit, gateway expiry, ``/new``): close, summarise, forget."""
    tracer = get_tracer()
    debug_log(f"on_session_finalize fired: session={session_id}, reason={reason}")
    if not tracer.is_enabled or not session_id:
        return
    _finalize_session(tracer, session_id, platform, reason or "finalize", event="finalize")


@_fail_open
def on_session_reset(
    session_id: Optional[str] = None,
    reason: str = "",
    platform: str = "",
    old_session_id: Optional[str] = None,
    new_session_id: Optional[str] = None,
    **kwargs,
):
    """A new session replaces an old one (``/new``, ``/reset``): finalize the old
    one if it is still known and chain the new one to it."""
    tracer = get_tracer()
    debug_log(
        f"on_session_reset fired: session={session_id}, old={old_session_id}, new={new_session_id}, reason={reason}"
    )
    if not tracer.is_enabled:
        return
    new_id = new_session_id or session_id
    old_id = old_session_id
    if not old_id and tracer.sessions.last_finalized and tracer.sessions.last_finalized != new_id:
        # The CLI finalizes the old session first and names only the new one here.
        old_id = tracer.sessions.last_finalized
    if (
        old_id
        and old_id != new_id
        and (
            tracer.spans.has_span(f"session:{old_id}")
            or tracer.sessions.peek(old_id) is not None
            or tracer.sessions.turn_number(old_id)
        )
    ):
        _finalize_session(tracer, old_id, platform, reason or "reset", event="reset")
    if new_id and old_id:
        tracer.sessions.set_previous(new_id, old_id)


# ── Interruption and verification (#396) ─────────────────────────────────


@_fail_open
def on_agent_loop_stopped(
    session_key: Any = None,
    platform: Any = None,
    reason: Any = None,
    invalidation_reason: Any = None,
    **kwargs: Any,
):
    """The gateway stopped a running turn (``/stop``, the ``/new`` fast path).

    The turn is not ended here: Hermes usually still finishes it with
    ``on_session_end(interrupted=True)``, which carries the turn's usage. The
    root is marked instead, so it says why, and so the orphan sweep ends it
    ``interrupted`` rather than ``timed_out`` if no end ever arrives.
    """
    debug_log(f"agent_loop_stopped fired: key={session_key} reason={reason}")
    tracer = get_tracer()
    if not tracer.is_enabled:
        return
    session_id = tracer.sessions.session_for_key(str(session_key or "").strip())
    if not session_id:
        return
    text = truncate_string(str(reason or invalidation_reason or "stopped"), 120)
    ps = tracer.sessions.peek(session_id)
    if ps is not None:
        ps.note_stop(text)
    root = tracer.spans.get_span(f"session:{session_id}")
    if root is not None and hasattr(root, "set_attribute"):
        try:
            root.set_attribute("hermes.turn.stop_reason", text)
        except Exception:
            pass


@_fail_open
def on_pre_verify(session_id: Any = None, **kwargs: Any) -> None:
    """Count verification nudges (the agent edited code and is about to finish).

    ``pre_verify`` is a decision hook: a dict return keeps the turn going.
    This observer always returns None, and never records the changed paths
    or the final response.
    """
    debug_log(f"pre_verify fired: session={session_id} attempt={kwargs.get('attempt')}")
    tracer = get_tracer()
    if not tracer.is_enabled or not session_id:
        return None
    ps = tracer.sessions.peek(str(session_id))
    if ps is not None:
        ps.note_verify()
    return None
