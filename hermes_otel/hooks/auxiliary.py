"""Auxiliary LLM calls: the ``api.<model>`` spans Hermes makes on the agent's behalf (#389).

``pre_auxiliary_call`` / ``post_auxiliary_call`` fire once per physical provider
attempt at the relay boundary of ``agent.auxiliary_client`` — the funnel every
auxiliary task shares: session titling, context compression, Mixture-of-Agents
reference models and aggregator, vision, approval classification, the kanban
decomposer and triage specifier, the ``/goal`` judge, ... They carry the
``pre_api_request`` / ``post_api_request`` payload shape plus ``aux_task`` and
the identity of the main turn they run under (empty for turn-less callers such
as the kanban dispatcher or a gateway's idle work).

Each attempt is an ``api.{model}`` span tagged ``hermes.query_source=auxiliary``
and ``hermes.auxiliary.task=<aux_task>``, nested under whatever the owning turn
has in flight (or its own root when there is no turn), with the same token,
cost and duration attributes as a main-loop API call (``api_call.py`` builds
them for both). Usage is accounted on the turn separately from the main-loop
totals (see ``PerSession.add_auxiliary``).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .. import log_events as EV
from ..debug_utils import debug_log
from ..helpers import (
    QUERY_SOURCE_AUXILIARY,
    auxiliary_span_key,
    auxiliary_task_name,
    serialize_full,
    truncate_string,
)
from ._common import _emit_event, _fail_open, as_dict, get_tracer
from .api_call import (
    HookPayload,
    add_exception_event,
    apply_usage,
    duration_seconds,
    ensure_span,
    record_operation_duration,
    request_attributes,
    response_attributes,
    token_suffix,
)
from .attributes import (
    _gen_ai_attributes,
    _metric_model_labels,
    _model_attributes,
    _provider_attributes,
    _response_model_attributes,
)
from .usage import genai_metric_dims


def _owning_session(session_id: Any) -> Optional[str]:
    """The main turn's session id, or None for a turn-less auxiliary call."""
    text = str(session_id or "").strip()
    return text or None


def _span_name(model: Any) -> str:
    return f"api.{model}" if model else "api.auxiliary"


@_fail_open
def on_pre_auxiliary_call(
    aux_task: str = "",
    session_id: str = "",
    model: str = "",
    provider: str = "",
    api_mode: str = "",
    api_request_id: str = "",
    started_at: Any = None,
    message_count: int = 0,
    tool_count: int = 0,
    approx_input_tokens: int = 0,
    max_tokens: Any = None,
    streaming: Any = False,
    **kwargs,
):
    """Open an ``api.{model}`` span for one auxiliary provider attempt."""
    task = auxiliary_task_name(aux_task)
    debug_log(f"pre_auxiliary_call fired: task={task}, model={model}, session={session_id}")
    tracer = get_tracer()
    if not tracer.is_enabled:
        return

    tracer.sweep_expired_turns()

    owner = _owning_session(session_id)
    key = auxiliary_span_key(api_request_id, task, started_at)

    attributes = request_attributes(
        tracer,
        HookPayload(kwargs),
        session_id=owner,
        model=model,
        provider=provider,
        message_count=message_count,
        approx_input_tokens=approx_input_tokens,
        max_tokens=max_tokens,
    )
    attributes["hermes.query_source"] = QUERY_SOURCE_AUXILIARY
    attributes["hermes.auxiliary.task"] = task
    if api_mode:
        attributes["llm.api_mode"] = truncate_string(api_mode, 80)
    if tool_count:
        attributes["llm.request.tool_count"] = tool_count
    if streaming:
        attributes["gen_ai.request.stream"] = True

    # Not pushed as a parent: nothing runs *inside* an auxiliary attempt. The
    # MoA aggregator's tool calls belong to the main loop's api span.
    tracer.start_span(
        name=_span_name(model),
        key=key,
        kind="llm",
        attributes=attributes,
        session_id=owner,
    )
    debug_log(f"  auxiliary span started: key={key}")


@_fail_open
def on_post_auxiliary_call(
    aux_task: str = "",
    session_id: str = "",
    model: str = "",
    provider: str = "",
    base_url: str = "",
    api_mode: str = "",
    api_request_id: str = "",
    started_at: Any = None,
    api_duration: Any = None,
    finish_reason: Any = None,
    response_model: Any = None,
    usage: Any = None,
    error: Any = None,
    error_type: Any = None,
    streaming: Any = False,
    assistant_content_chars: Any = 0,
    assistant_tool_call_count: Any = 0,
    **kwargs,
):
    """End the auxiliary ``api.{model}`` span with usage, cost and outcome."""
    task = auxiliary_task_name(aux_task)
    debug_log(f"post_auxiliary_call fired: task={task}, model={model}, error={error_type}")
    tracer = get_tracer()
    if not tracer.is_enabled:
        return

    owner = _owning_session(session_id)
    key = auxiliary_span_key(api_request_id, task, started_at)
    failed = error is not None or bool(error_type)
    err_type = truncate_string(error_type, 200) if error_type else ("error" if failed else "")
    err_message = truncate_string(error, 500) if error else ""

    attributes: Dict[str, Any] = {
        "hermes.query_source": QUERY_SOURCE_AUXILIARY,
        "hermes.auxiliary.task": task,
    }
    attributes.update(_gen_ai_attributes(owner, "chat"))
    attributes.update(_provider_attributes(provider))
    attributes.update(_response_model_attributes(response_model))
    if err_type:
        attributes["error.type"] = err_type

    model_labels = {**_metric_model_labels(model, provider), "query_source": QUERY_SOURCE_AUXILIARY}
    totals, cost = apply_usage(
        tracer,
        as_dict(usage),
        attributes,
        model=model,
        provider=provider,
        base_url=base_url,
        response_model=response_model,
        model_labels=model_labels,
        cache_labels={**_metric_model_labels(model, provider), "api_mode": api_mode},
    )
    if owner:
        tracer.sessions.get_or_create(owner).add_auxiliary(totals, cost, failed=failed)

    duration = duration_seconds(api_duration)
    attributes.update(
        response_attributes(
            api_duration=duration,
            finish_reason=finish_reason,
            assistant_content_chars=assistant_content_chars,
            assistant_tool_call_count=assistant_tool_call_count,
        )
    )
    record_operation_duration(
        tracer,
        duration,
        genai_metric_dims(model, provider, response_model),
        error_type=err_type or None,
    )
    if streaming:
        attributes["gen_ai.request.stream"] = True

    if tracer.config.captures_full_responses():
        response = as_dict(kwargs.get("response"))
        message = as_dict(response.get("assistant_message")) if response else {}
        content = message.get("content")
        if isinstance(content, str) and content:
            attributes["output.value"] = content
            attributes["output.mime_type"] = "text/plain"
            attributes["gen_ai.output.messages"] = serialize_full(
                [{"role": "assistant", "content": content}]
            )
            attributes["hermes.content.output_chars"] = len(content)

    tracer.record_metric(
        "auxiliary_count",
        1,
        {
            **_metric_model_labels(model, provider),
            "aux_task": task,
            "status": "error" if failed else "ok",
        },
    )

    # The in-flight span, or (post without pre: the pre fired before the plugin
    # loaded, or the attempt outlived the orphan sweep) a fresh one so the
    # spend is still visible with the identity the pre hook would have set.
    span, created = ensure_span(
        tracer, key, name=_span_name(model), session_id=owner, model=model, provider=provider
    )
    if not created and failed:
        add_exception_event(span, err_type or "error", err_message, keep_empty_message=True)

    _emit_event(
        tracer,
        EV.AUXILIARY_CALL,
        f"auxiliary {task} {model}{token_suffix(attributes)}"
        + (f": {err_type}" if err_type else ""),
        {**attributes, **_model_attributes(model, provider)},
        severity="ERROR" if failed else "INFO",
        session_id=owner,
        span=tracer.spans.get_span(key),
        content=False,
    )

    tracer.end_span(
        key,
        attributes=attributes,
        status="error" if failed else "ok",
        error_message=(err_message or err_type or "auxiliary call failed") if failed else None,
    )
    debug_log(f"  auxiliary span ended: key={key}, status={'error' if failed else 'ok'}")
