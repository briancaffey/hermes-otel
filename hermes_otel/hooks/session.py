"""Session hooks: the turn root (``agent`` / ``cron`` span) and its summary."""

from __future__ import annotations

from typing import Any, Dict

from ..debug_utils import debug_log
from ..helpers import detect_session_kind, truncate_string
from ._common import _fail_open, get_tracer
from .attributes import (
    _correlation_attributes,
    _extract_correlation_id,
    _gen_ai_attributes,
    _model_attributes,
    _per_session_sender_attributes,
    _platform_attributes,
    _response_model_attributes,
    _session_identity_attributes,
    _summary_attributes,
    _weave_turn_attributes,
)
from .usage import _genai_metric_dims, _record_genai_token_usage, _usage_attributes

try:
    from opentelemetry.trace import Link
except ImportError:  # pragma: no cover — plugin is unusable without OTel
    Link = None  # type: ignore[assignment]


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
    if not ps.correlation_id:
        ps.correlation_id = _extract_correlation_id(extra_kwargs)

    attributes: Dict[str, Any] = {"hermes.session.kind": kind}
    attributes.update(_session_identity_attributes(session_id, root=True))
    attributes.update(_platform_attributes(platform))
    # Hermes passes no provider on on_session_start; a host that does is honoured.
    # Otherwise the provider lands on the root at on_session_end, once the
    # turn's API calls have reported it (#153).
    attributes.update(_model_attributes(model, extra_kwargs.get("provider")))
    attributes.update(_gen_ai_attributes(session_id, "invoke_agent"))
    attributes.update(_weave_turn_attributes(session_id, extra_kwargs))
    attributes.update(_correlation_attributes(tracer, session_id, extra_kwargs))
    if synthesized:
        attributes["hermes.session.synthesized"] = True

    cron_job_id = extra_kwargs.get("job_id") or extra_kwargs.get("cron_job_id")
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
        if record.get("role"):
            attributes["hermes.subagent.role"] = truncate_string(record["role"], 200)
        if record.get("parent_session_id"):
            attributes["hermes.subagent.parent_session_id"] = truncate_string(
                record["parent_session_id"], 200
            )
        span_obj = record.get("span")
        if span_obj is not None and hasattr(span_obj, "get_span_context"):
            parent_override = span_obj
        elif record.get("context") is not None and Link is not None:
            links = [Link(record["context"])]

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
    tracer.record_metric("session_count", 1, {"platform": platform or "unknown"})
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
    # Provider: what this turn's API calls reported, else what the host passed.
    provider = (ps.provider if ps is not None else "") or kwargs.get("provider")
    attributes.update(_model_attributes(model, provider))
    attributes.update(_response_model_attributes(ps.response_model if ps is not None else ""))
    attributes.update(_gen_ai_attributes(session_id, "invoke_agent"))
    attributes.update(_weave_turn_attributes(session_id, kwargs))
    attributes.update(_correlation_attributes(tracer, session_id, kwargs))

    if ps is not None and ps.io_captured:
        if ps.io.get("input"):
            attributes["input.value"] = ps.io["input"]
        if ps.io.get("output"):
            attributes["output.value"] = ps.io["output"]

    if ps is not None and ps.usage_updated:
        attributes.update(_usage_attributes(ps.usage))
        # OTel GenAI agent-level token-usage histogram (per-turn/session rollup).
        # Prefer the provider captured from this session's API calls over the
        # platform, so the dimension matches the gen_ai.client.* metrics.
        if tracer.config.emit_genai_metrics:
            agent_provider = ps.provider or kwargs.get("provider") or platform
            _record_genai_token_usage(
                tracer,
                "gen_ai.agent.token.usage",
                ps.usage,
                _genai_metric_dims(model, agent_provider, model, operation="invoke_agent"),
            )

    # Surface the last API error's type on the root so a failed turn shows why.
    if ps is not None and ps.last_error_type:
        attributes["error.type"] = ps.last_error_type

    attributes.update(_per_session_sender_attributes(ps))
    if ps is not None and ps.turn_number:
        attributes["hermes.turn.number"] = ps.turn_number

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

    tracer.spans.pop_parent(session_id=session_id)
    tracer.end_span(key, attributes=attributes, status=status)
    tracer.unregister_turn(session_id)

    # End of a user-visible unit of work. Flush so the trace is visible in
    # the backend UI immediately rather than after schedule_delay_millis.
    # Honors config.force_flush_on_session_end for users who'd rather let
    # the batcher do its thing even at turn boundaries.
    if tracer.config.force_flush_on_session_end:
        tracer._force_flush()

    debug_log(f"  session span ended: key={key}, status={status}")
