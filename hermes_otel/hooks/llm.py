"""LLM hooks: the per-turn ``llm.<model>`` span and the per-request ``api.<model>`` spans."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .. import log_events as EV
from ..coerce import coerce_bool, to_optional_int
from ..debug_utils import debug_log
from ..helpers import truncate_string
from ..instruments import COMPAT_SESSION_LABEL
from ._common import (
    _emit_event,
    _fail_open,
    _mark_truncated,
    _preview_for,
    _preview_marked,
    as_dict,
    get_tracer,
)
from .api_call import (
    HookPayload,
    add_exception_event,
    apply_usage,
    ensure_span,
    full_response_attributes,
    query_source_for,
    record_first_chunk,
    record_operation_duration,
    request_attributes,
    response_attributes,
    token_suffix,
)
from .attributes import (
    _ID_MAX,
    _gen_ai_attributes,
    _message_json,
    _metric_model_labels,
    _model_attributes,
    _provider_attributes,
    _response_model_attributes,
    _sender_attributes,
    _serialize_conversation_history,
    _session_identity_attributes,
    _turn_attributes,
    correlation_attributes,
)
from .session import _start_session_span
from .usage import genai_metric_dims


def count_user_turns(conversation: Any) -> int:
    """User prompts in a conversation (``role == "user"`` messages), or 0 when
    it is not a list. Hermes passes the turn's whole conversation, current
    prompt included, to ``pre_llm_call``; after a context compaction it holds
    fewer, so callers treat the count as a floor."""
    if not isinstance(conversation, (list, tuple)):
        return 0
    return sum(1 for m in conversation if isinstance(m, dict) and m.get("role") == "user")


def http_status_class(status_code: Any) -> str:
    """Bucket an HTTP status code into a low-cardinality metric label.

    Returns ``"2xx"``/``"3xx"``/``"4xx"``/``"5xx"`` for a valid HTTP code,
    ``"network"`` when there is no code (None / 0 / unparseable — i.e. the
    request never got an HTTP response: timeout, connection error), and
    ``"other"`` for an out-of-range number. Keeps error-metric cardinality
    bounded regardless of provider quirks.
    """
    code = to_optional_int(status_code)
    if code is None or code <= 0:
        return "network"
    if 100 <= code < 600:
        return f"{code // 100}xx"
    return "other"


def _llm_input_attributes(tracer, user_message: Any) -> Dict[str, Any]:
    """``input.value`` + ``gen_ai.input.messages`` for the latest user message."""
    attrs: Dict[str, Any] = {}
    preview, original = _preview_marked(tracer, "llm_input", user_message)
    if preview is not None:
        attrs["input.value"] = preview
        messages = _message_json("user", preview)
        if messages is not None:
            attrs["gen_ai.input.messages"] = messages
        _mark_truncated(attrs, "input", original)
    return attrs


@_fail_open
def on_pre_llm_call(
    session_id: str,
    user_message: str,
    conversation_history: list,
    is_first_turn: bool,
    model: str,
    platform: str,
    **kwargs,
):
    """Start an LLM span before the model is called."""
    debug_log(f"pre_llm_call fired: model={model}, session={session_id}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return None

    tracer.sweep_expired_turns()

    # The previous turn failed on every API attempt and never ended (#467):
    # close it so this turn gets its own root instead of joining that trace.
    if session_id:
        tracer.close_failed_turn(session_id)

    # hermes fires on_session_start only on the very first turn, but
    # on_session_end fires per turn. On continuation turns (2+) we arrive
    # here with no active session span → llm.* would become the trace
    # root. Synthesize one so every turn is rooted under agent/cron.
    if session_id and not tracer.spans.has_span(f"session:{session_id}"):
        _start_session_span(session_id, model, platform, kwargs, synthesized=True)

    key = f"llm:{session_id}"

    # One pre_llm_call per user prompt (Hermes fires it before the tool loop),
    # so this is where the turn gets its number. Tool and api spans opened
    # later in the turn pick it up via _turn_attributes.
    turn_number = tracer.sessions.next_turn(
        session_id,
        reset=bool(is_first_turn),
        history_turns=count_user_turns(conversation_history),
    )
    if session_id:
        _emit_event(
            tracer,
            EV.TURN_START,
            f"turn {turn_number} started",
            {
                "hermes.turn.number": turn_number,
                "hermes.platform": platform or None,
                **_model_attributes(model, kwargs.get("provider")),
            },
            session_id=session_id,
            span=tracer.spans.get_session_root(session_id),
            content=False,
        )

    # The turn's aggregator: the first LLM input for the top-level session
    # span, the sender, and the provider its earlier API calls reported.
    ps = tracer.sessions.get_or_create(session_id) if session_id else None
    if ps is not None:
        ps.capture_input(_preview_for(tracer, "llm_input", user_message))

    # OpenInference attributes — Phoenix Info panel. The provider is only
    # known once an API call has reported it (continuation turns); the
    # platform is not a provider (#153).
    attributes: Dict[str, Any] = {}
    attributes.update(_session_identity_attributes(session_id))
    attributes.update(_model_attributes(model, ps.provider if ps is not None else None))
    attributes.update(_gen_ai_attributes(session_id, "chat"))
    attributes.update(correlation_attributes(tracer, session_id, kwargs))
    attributes.update(_turn_attributes(tracer, session_id))

    if tracer.config.capture_sender_id:
        sender_id = (
            truncate_string(kwargs.get("sender_id"), _ID_MAX) if kwargs.get("sender_id") else ""
        )
        if sender_id:
            sender_attrs = _sender_attributes(sender_id, truncate_string(platform, 120))
            attributes.update(sender_attrs)
            if ps is not None:
                ps.set_sender(sender_id, sender_attrs["user.id"])

    # Opt-in: put the entire conversation the model is about to see on
    # input.value. Falls back to just the latest user_message otherwise —
    # that's the historical default and what small backends handle best.
    full = None
    if tracer.config.capture_conversation_history and tracer.config.captures_content():
        full = _serialize_conversation_history(
            conversation_history, tracer.config.conversation_history_max_chars
        )
    if full is not None:
        attributes["input.value"] = full
        attributes["input.mime_type"] = "application/json"
        attributes["gen_ai.input.messages"] = full
        attributes["hermes.conversation.message_count"] = len(conversation_history)
    else:
        attributes.update(_llm_input_attributes(tracer, user_message))

    span = tracer.start_span(
        name=f"llm.{model}",
        key=key,
        kind="llm",
        attributes=attributes,
        session_id=session_id,
    )

    # Push as parent — tool spans during this LLM call will nest under it
    tracer.spans.push_parent(span, session_id=session_id)
    debug_log(f"  LLM span started: key={key}")
    return None  # Don't inject context, just observe


@_fail_open
def on_post_llm_call(
    session_id: str,
    user_message: str,
    assistant_response: str,
    conversation_history: list,
    model: str,
    platform: str,
    **kwargs,
):
    """End the LLM span and record the response."""
    debug_log(f"post_llm_call fired: model={model}, session={session_id}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return

    key = f"llm:{session_id}"
    debug_log(f"  ending span: key={key}")

    preview, original = _preview_marked(tracer, "llm_output", assistant_response)

    # Capture last LLM output for the top-level session span.
    ps = tracer.sessions.peek(session_id) if session_id else None
    if ps is not None:
        ps.capture_output(preview)

    # Provider / response model: what this turn's API calls reported, never
    # the platform or the request model (#153, #155).
    provider = ps.provider if ps is not None else ""
    tracer.record_metric("message_count", 1, _metric_model_labels(model, provider))

    # OpenInference attributes — Phoenix Info panel
    attributes: Dict[str, Any] = {}
    attributes.update(_session_identity_attributes(session_id))
    attributes.update(_response_model_attributes(ps.response_model if ps is not None else ""))
    # Both conventions for the provider (llm.provider + the gen_ai pair).
    attributes.update(_model_attributes(None, provider))
    attributes.update(_gen_ai_attributes(session_id, "chat"))
    attributes.update(correlation_attributes(tracer, session_id, kwargs))
    if preview is not None:
        attributes["output.value"] = preview
        messages = _message_json("assistant", preview)
        if messages is not None:
            attributes["gen_ai.output.messages"] = messages
        _mark_truncated(attributes, "output", original)

    # Pop parent — tool spans after this won't nest under this LLM call
    tracer.spans.pop_parent(session_id=session_id)

    # Mark as OK — LLM call completed successfully
    tracer.end_span(key, attributes=attributes, status="ok")
    debug_log("  LLM span ended: status=ok")


def _query_source(tracer, session_id: Optional[str]) -> str:
    """``main`` or ``subagent``: which loop this session's API calls belong to (#389)."""
    ps = tracer.sessions.peek(session_id) if session_id else None
    return query_source_for(ps is not None and ps.is_subagent)


@_fail_open
def on_pre_api_request(
    task_id: str,
    session_id: str,
    platform: str,
    model: str,
    provider: str,
    base_url: str,
    api_mode: str,
    api_call_count: int,
    message_count: int,
    tool_count: int,
    approx_input_tokens: int,
    request_char_count: int,
    max_tokens: int,
    **kwargs,
):
    """Fires before each individual LLM API request."""
    debug_log(f"pre_api_request fired: model={model}, provider={provider}, session={session_id}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return

    tracer.sweep_expired_turns()

    key = f"api:{task_id}"

    if session_id:
        tracer.sessions.get_or_create(session_id).turn_summary.add_api_call()

    attributes = request_attributes(
        tracer,
        HookPayload(kwargs),
        session_id=session_id,
        model=model,
        provider=provider,
        message_count=message_count,
        approx_input_tokens=approx_input_tokens,
        max_tokens=max_tokens,
    )
    attributes["llm.api_mode"] = api_mode
    attributes["hermes.query_source"] = _query_source(tracer, session_id)

    span = tracer.start_span(
        name=f"api.{model}",
        key=key,
        kind="llm",
        attributes=attributes,
        session_id=session_id,
    )

    # Push as parent — tool spans during this API call will nest under it
    tracer.spans.push_parent(span, session_id=session_id)
    debug_log(f"  API span started: key={key}")


@_fail_open
def on_post_api_request(
    task_id: str,
    session_id: str,
    platform: str,
    model: str,
    provider: str,
    base_url: str,
    api_mode: str,
    api_call_count: int,
    api_duration: float,
    finish_reason: str,
    message_count: int,
    response_model: str,
    usage: dict,
    assistant_content_chars: int,
    assistant_tool_call_count: int,
    **kwargs,
):
    """Fires after each individual LLM API request with usage stats."""
    debug_log(f"post_api_request fired: model={model}, finish={finish_reason}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return

    key = f"api:{task_id}"
    debug_log(f"  ending span: key={key}, usage={usage}")
    payload = HookPayload(kwargs)

    attributes: Dict[str, Any] = {}
    attributes.update(_gen_ai_attributes(session_id, "chat"))
    attributes.update(_provider_attributes(provider))
    # Only a reported response model; the request model is not a response (#155).
    attributes.update(_response_model_attributes(response_model))
    if payload.response_id:
        attributes["gen_ai.response.id"] = truncate_string(payload.response_id, _ID_MAX)
    attributes.update(correlation_attributes(tracer, session_id, kwargs))

    model_labels = _metric_model_labels(model, provider)
    model_labels["query_source"] = _query_source(tracer, session_id)
    # Only the opt-in claude_code.* instruments keep this (record_metric
    # strips it for everything else, #485).
    model_labels[COMPAT_SESSION_LABEL] = session_id

    # Token usage (dual convention), the usage/cache/GenAI metrics and the
    # cost estimate; then the turn's roll-up on the session.
    usage = as_dict(usage)
    totals, cost = apply_usage(
        tracer,
        usage,
        attributes,
        model=model,
        provider=provider,
        base_url=base_url,
        response_model=response_model,
        model_labels=model_labels,
        cache_labels={**model_labels, "api_mode": api_mode},
    )
    if session_id:
        # An aggregator is minted only when the call reported usage; a bare
        # success still clears #467's flag on an existing one.
        ps = tracer.sessions.peek(session_id)
        if ps is None and totals is not None:
            ps = tracer.sessions.get_or_create(session_id)
        if ps is not None:
            ps.record_attempt(totals, cost, provider=provider, response_model=response_model)

    attributes.update(
        response_attributes(
            api_duration=api_duration,
            finish_reason=finish_reason,
            assistant_content_chars=assistant_content_chars,
            assistant_tool_call_count=assistant_tool_call_count,
        )
    )
    record_operation_duration(
        tracer, api_duration, genai_metric_dims(model, provider, response_model)
    )
    record_first_chunk(tracer, attributes, payload, _metric_model_labels(model, provider))
    attributes.update(full_response_attributes(tracer, payload))

    # The GenAI semconv inference event (#267): request attributes from the api
    # span's start, response attributes from this hook, content gated by
    # logs.events.content. Read the span before end_span forgets it.
    api_span = tracer.spans.get_span(key)
    if api_span is not None and tracer.config.log_events:
        start_attrs = dict(getattr(api_span, "attributes", None) or {})
        event_attrs: Dict[str, Any] = {
            k: v
            for k, v in {**start_attrs, **attributes}.items()
            if k.startswith(EV.INFERENCE_PREFIXES) or k in EV.INFERENCE_KEYS
        }
        _emit_event(
            tracer,
            EV.INFERENCE_DETAILS,
            f"{event_attrs.get('gen_ai.operation.name') or 'chat'} {model}"
            + token_suffix(event_attrs),
            event_attrs,
            session_id=session_id,
            span=api_span,
        )

    # Pop parent
    tracer.spans.pop_parent(session_id=session_id)

    # Mark as OK
    tracer.end_span(key, attributes=attributes, status="ok")
    debug_log(f"  API span ended: status=ok, tokens={usage.get('total_tokens', 0) if usage else 0}")


def _error_attributes(
    error_type: str, status_code: Any, retry_count: Any, max_retries: Any, retryable: Optional[bool]
) -> Dict[str, Any]:
    """The failure's own attributes: each value Hermes reported under its
    key(s), absent when it sent nothing usable."""
    attributes: Dict[str, Any] = {}
    if error_type:
        attributes["error.type"] = error_type
    for value, keys in (
        (
            to_optional_int(status_code),
            ("http.response.status_code", "gen_ai.response.status_code"),
        ),
        (to_optional_int(retry_count), ("hermes.retry.count",)),
        (to_optional_int(max_retries), ("hermes.max_retries",)),
        (retryable, ("hermes.retryable",)),
    ):
        if value is not None:
            attributes.update(dict.fromkeys(keys, value))
    return attributes


@_fail_open
def on_api_request_error(
    task_id: str = None,
    session_id: str = None,
    platform: str = None,
    model: str = None,
    provider: str = None,
    api_duration: float = None,
    status_code: Any = None,
    retry_count: Any = None,
    max_retries: Any = None,
    retryable: Any = None,
    reason: str = None,
    error: dict = None,
    **kwargs,
):
    """Fires when a provider API request fails (rate limit, timeout, 5xx, ...).

    Without this hook the ``api.{model}`` span opened by ``on_pre_api_request``
    is never closed on failure — it ends ``OK`` via the orphan sweep, hiding the
    error. Here we close it as ``ERROR`` with a recorded exception and retry
    metadata, and record error/retry metrics. Fails open.
    """
    debug_log(
        f"api_request_error fired: model={model}, status_code={status_code}, "
        f"retry_count={retry_count}, retryable={retryable}"
    )
    tracer = get_tracer()
    if not tracer.is_enabled:
        return

    tracer.sweep_expired_turns()

    if isinstance(error, BaseException):
        error = {"type": type(error).__name__, "message": str(error)}
    error = as_dict(error)
    error_type = truncate_string(error.get("type"), _ID_MAX) if error.get("type") else ""
    error_message = truncate_string(error.get("message") or reason, 500)
    status_class = http_status_class(status_code)
    is_retryable = coerce_bool(retryable)

    # The error attributes that go on whichever span we close.
    attributes = _error_attributes(error_type, status_code, retry_count, max_retries, is_retryable)
    attributes.update(response_attributes(api_duration=api_duration))
    attributes.update(_gen_ai_attributes(session_id, "chat"))
    attributes.update(_provider_attributes(provider or platform))
    attributes.update(correlation_attributes(tracer, session_id, kwargs))

    # Remember why this turn failed so on_session_end can surface it on the root.
    ps = tracer.sessions.peek(session_id) if session_id else None
    if ps is not None:
        ps.record_api_failure(error_type)

    _emit_event(
        tracer,
        EV.API_ERROR,
        f"api error {error_type or 'unknown'}"
        + (f" ({status_class})" if status_class else "")
        + (f": {error_message}" if error_message else ""),
        {
            **attributes,
            "exception.type": error_type or None,
            "exception.message": error_message or None,
        },
        severity="WARN" if is_retryable else "ERROR",
        session_id=session_id,
        content=False,
    )

    # The in-flight api span, or a short-lived one when the error came before
    # pre_api_request (or the span was already swept) so the failure stays visible.
    key = f"api:{task_id}" if task_id else None
    fallback_key = key or f"api.error:{kwargs.get('api_request_id') or session_id or 'unknown'}"
    span, created_fallback = ensure_span(
        tracer,
        fallback_key,
        name=f"api.{model}" if model else "api.error",
        session_id=session_id,
        model=model,
        provider=provider,
    )
    key = fallback_key

    # Record an OTel exception event (semconv: an event named "exception").
    add_exception_event(span, error_type or "error", error_message)

    # The in-flight api span was pushed as the parent in on_pre_api_request;
    # balance the stack (the fallback span was never pushed).
    if not created_fallback:
        tracer.spans.pop_parent(session_id=session_id)

    tracer.end_span(
        key,
        attributes=attributes,
        status="error",
        error_message=error_message or reason or error_type or "api request failed",
    )

    # Metrics. Keep labels low-cardinality. ``retryable`` keeps the three-way
    # answer coerce_bool gives (unknown when Hermes sent nothing usable).
    model_labels = _metric_model_labels(model, provider)
    metric_attrs: Dict[str, Any] = {
        "error_type": error_type or "unknown",
        "status_class": status_class,
        "retryable": "unknown" if is_retryable is None else str(is_retryable).lower(),
        **model_labels,
    }
    tracer.record_metric("api_error_count", 1, metric_attrs)
    # Count a retry attempt only when the failure was actually retryable.
    if is_retryable:
        tracer.record_metric("retry_count", 1, dict(model_labels))

    # The GenAI operation-duration histogram on the failure path.
    record_operation_duration(
        tracer, api_duration, genai_metric_dims(model, provider), error_type=error_type or "unknown"
    )

    debug_log(f"  API error span ended: key={key}, error.type={error_type}, class={status_class}")
