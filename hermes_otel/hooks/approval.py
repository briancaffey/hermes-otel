"""Approval hooks: one ``approval.<pattern>`` span per human / guardian decision wait."""

from __future__ import annotations

import time
from typing import Any, Dict

from ..debug_utils import debug_log
from ..helpers import classify_approval_choice, clip_joined, truncate_string
from ._common import _fail_open, _preview_for, _resolve_session_id, get_tracer
from .attributes import _gen_ai_attributes


def _approval_span_key(session_id: str, tool_call_id: Any, pattern_key: str) -> str:
    """Stable key for an approval span; correlated to the triggering tool call."""
    return f"approval:{session_id}:{tool_call_id or pattern_key or 'cmd'}"


@_fail_open
def on_pre_approval_request(
    command: str = None,
    description: str = None,
    pattern_key: str = None,
    pattern_keys: list = None,
    session_key: str = None,
    surface: str = None,
    turn_id: str = None,
    tool_call_id: str = None,
    **kwargs,
):
    """Open a span while the agent blocks waiting on a human approval decision.

    Observer-only: the return value is ignored by the approval system, so this
    can never veto/alter an approval. Hermes sends no ``session_id`` on this
    hook, so the session comes from ``turn_id`` (which embeds it); the tool is
    correlated via ``tool_call_id``. Fails open.
    """
    debug_log(f"pre_approval_request fired: pattern={pattern_key}, surface={surface}")
    tracer = get_tracer()
    if not tracer.is_enabled:
        return

    tracer.sweep_expired_turns()

    session_id = _resolve_session_id(kwargs, turn_id=turn_id)
    # No pattern key reported → plain ``approval`` span without the attribute;
    # a placeholder key would read like a real rule (#155).
    pk = truncate_string(pattern_key, 200) if pattern_key else ""
    key = _approval_span_key(session_id, tool_call_id, pk)

    attributes: Dict[str, Any] = {"hermes.span_kind": "approval"}
    if pk:
        attributes["hermes.approval.pattern_key"] = pk
    if tool_call_id:
        # Correlates the approval to the tool span / call it gates.
        attributes["gen_ai.tool.call.id"] = truncate_string(tool_call_id, 200)
    if surface:
        attributes["hermes.approval.surface"] = truncate_string(surface, 60)
    if pattern_keys:
        attributes["hermes.approval.pattern_keys"] = clip_joined(
            [str(k) for k in pattern_keys], ","
        )
    # command / description follow the same preview policy as tool input.
    cmd = _preview_for(tracer, "tool_input", command)
    if cmd is not None:
        attributes["hermes.approval.command"] = cmd
    if description:
        desc = _preview_for(tracer, None, description)
        if desc is not None:
            attributes["hermes.approval.description"] = desc
    attributes.update(_gen_ai_attributes(session_id, "approval"))

    tracer.spans.record_approval_start(key, time.perf_counter())
    tracer.start_span(
        name=f"approval.{pk}" if pk else "approval",
        key=key,
        kind="general",
        attributes=attributes,
        session_id=session_id or None,
        parent=tracer.spans.get_current_parent(session_id or None),
    )
    debug_log(f"  approval span opened: key={key}")


@_fail_open
def on_post_approval_response(
    command: str = None,
    description: str = None,
    pattern_key: str = None,
    pattern_keys: list = None,
    session_key: str = None,
    surface: str = None,
    choice: str = None,
    turn_id: str = None,
    tool_call_id: str = None,
    **kwargs,
):
    """Close the approval span with the human's decision + wait duration."""
    debug_log(f"post_approval_response fired: pattern={pattern_key}, choice={choice}")
    tracer = get_tracer()
    if not tracer.is_enabled:
        return

    session_id = _resolve_session_id(kwargs, turn_id=turn_id)
    pk = truncate_string(pattern_key, 200) if pattern_key else ""
    key = _approval_span_key(session_id, tool_call_id, pk)

    verdict = classify_approval_choice(choice, kwargs.get("decided_by"))
    attributes: Dict[str, Any] = {
        "hermes.approval.granted": verdict["granted"],
        "hermes.approval.timed_out": verdict["timed_out"],
    }
    if verdict["choice"]:
        attributes["hermes.approval.choice"] = verdict["choice"]
    if verdict["decided_by"]:
        # Provenance: human answer vs. smart-guardian (aux LLM) verdict.
        attributes["hermes.approval.decided_by"] = verdict["decided_by"]

    start = tracer.spans.pop_approval_start(key)
    duration_ms = None
    if start is not None:
        duration_ms = (time.perf_counter() - start) * 1000
        attributes["hermes.approval.duration_ms"] = round(duration_ms, 1)

    # A denied or timed-out approval is a valid human outcome, not an error.
    tracer.end_span(key, attributes=attributes, status="ok")

    # ``pattern_key`` is free-form (a command fragment) — never a metric label.
    metric_attrs = {"choice": verdict["choice"] or "unknown"}
    tracer.record_metric("approval_count", 1, metric_attrs)
    if duration_ms is not None:
        tracer.record_metric("approval_duration", duration_ms, metric_attrs)
    debug_log(f"  approval span ended: key={key}, choice={verdict['choice']}, dur={duration_ms}")
