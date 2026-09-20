"""Sub-agent delegation hooks: the ``subagent.<role>`` span in the parent's trace."""

from __future__ import annotations

from typing import Any, Dict

from ..debug_utils import debug_log
from ..helpers import subagent_span_key, subagent_status_to_span_status, truncate_string
from ._common import _fail_open, _preview_for, get_tracer
from .attributes import _correlation_attributes, _session_identity_attributes


@_fail_open
def on_subagent_start(
    parent_session_id: str = None,
    child_session_id: str = None,
    child_role: str = None,
    child_goal: str = None,
    **kwargs,
):
    """Open a delegation span when a parent agent spawns a child agent.

    The span lives in the *parent's* trace, nested under whatever the parent
    has in flight (its api/llm span for the turn that called ``delegate_task``).
    Its ``SpanContext`` is stashed by ``child_session_id`` so the child's own
    root span (created later from its own ``on_session_start``) rejoins this
    trace — see the sub-agent rejoin block in ``_start_session_span``.
    """
    debug_log(
        f"subagent_start fired: parent={parent_session_id}, child={child_session_id}, "
        f"role={child_role}"
    )
    tracer = get_tracer()
    if not tracer.is_enabled:
        return

    tracer.sweep_expired_turns()

    key = subagent_span_key(child_session_id)
    if key is None:
        # No child session id → nothing to correlate the child run back to.
        debug_log("  subagent_start: no child_session_id, skipping")
        return

    role = truncate_string(child_role, 200) if child_role else "subagent"
    span_name = f"subagent.{role}"

    attributes: Dict[str, Any] = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": role,
        "hermes.subagent.role": role,
        "hermes.subagent.child_session_id": truncate_string(child_session_id, 200),
    }
    attributes.update(_session_identity_attributes(parent_session_id))
    if parent_session_id:
        attributes["hermes.subagent.parent_session_id"] = truncate_string(parent_session_id, 200)
    for src, dest in (
        ("parent_turn_id", "hermes.subagent.parent_turn_id"),
        ("child_subagent_id", "hermes.subagent.child_id"),
        ("parent_subagent_id", "hermes.subagent.parent_id"),
    ):
        value = kwargs.get(src)
        if value:
            attributes[dest] = truncate_string(value, 200)
    goal_preview = _preview_for(tracer, "tool_input", child_goal)
    if goal_preview is not None:
        attributes["hermes.subagent.goal"] = goal_preview
        attributes["input.value"] = goal_preview
    attributes.update(_correlation_attributes(tracer, parent_session_id, kwargs))

    # Nest under the parent session's in-flight span (api/llm/session). The
    # delegation span is NOT pushed as a parent — the parent session keeps
    # working on its own stack; this span is a side branch that the child
    # rejoins via the tracker's sub-agent registry.
    span = tracer.start_span(
        name=span_name,
        key=key,
        kind="agent",
        attributes=attributes,
        session_id=parent_session_id,
    )

    record: Dict[str, Any] = {
        "span": span,
        "role": role,
        "parent_session_id": parent_session_id,
    }
    if span is not None and hasattr(span, "get_span_context"):
        try:
            record["context"] = span.get_span_context()
        except Exception:
            record["context"] = None
    tracer.spans.register_subagent(child_session_id, record)
    debug_log(f"  subagent span started: key={key}, name={span_name}")


@_fail_open
def on_subagent_stop(
    parent_session_id: str = None,
    child_session_id: str = None,
    child_role: str = None,
    child_summary: str = None,
    child_status: str = None,
    duration_ms: float = None,
    **kwargs,
):
    """Close the delegation span when a child agent returns or fails."""
    debug_log(
        f"subagent_stop fired: child={child_session_id}, status={child_status}, "
        f"duration_ms={duration_ms}"
    )
    tracer = get_tracer()
    if not tracer.is_enabled:
        return

    key = subagent_span_key(child_session_id)
    if key is None:
        debug_log("  subagent_stop: no child_session_id, skipping")
        return

    record = tracer.spans.pop_subagent(child_session_id)
    role = (record.get("role") if record else None) or (
        truncate_string(child_role, 200) if child_role else "subagent"
    )

    status = subagent_status_to_span_status(child_status)
    attributes: Dict[str, Any] = {}
    reported = truncate_string(child_status, 120) if child_status else ""
    if reported:
        attributes["hermes.subagent.status"] = reported
    if duration_ms is not None:
        try:
            attributes["hermes.subagent.duration_ms"] = round(float(duration_ms), 1)
        except (TypeError, ValueError):
            pass
    summary_preview = _preview_for(tracer, "tool_output", child_summary)
    if summary_preview is not None:
        attributes["hermes.subagent.summary"] = summary_preview
        attributes["output.value"] = summary_preview

    error_message = None
    if status == "error":
        error_message = truncate_string(child_summary or child_status, 500)

    tracer.end_span(key, attributes=attributes, status=status, error_message=error_message)

    # Metrics. ``status`` stays the coarse ok|error (existing dashboards filter
    # on it); ``child_status`` carries what Hermes reported, lower-cased — a
    # bounded set (completed / failed / timeout ...), so still low cardinality.
    metric_attrs: Dict[str, Any] = {"role": role, "status": status}
    if reported:
        metric_attrs["child_status"] = reported.strip().lower()[:60]
    tracer.record_metric("subagent_count", 1, metric_attrs)
    if duration_ms is not None:
        try:
            tracer.record_metric("subagent_duration", float(duration_ms), {"role": role})
        except (TypeError, ValueError):
            pass
    debug_log(f"  subagent span ended: key={key}, status={status}")
