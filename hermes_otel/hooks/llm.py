"""LLM hooks: the per-turn ``llm.<model>`` span and the per-request ``api.<model>`` spans."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..debug_utils import debug_log
from ..helpers import (
    coerce_bool,
    http_status_class,
    serialize_full,
    to_optional_int,
    truncate_string,
)
from ._common import (
    _as_dict,
    _fail_open,
    _mark_truncated,
    _preview_for,
    _preview_marked,
    get_tracer,
)
from .attributes import (
    _correlation_attributes,
    _gen_ai_attributes,
    _gen_ai_request_param_attributes,
    _message_json,
    _metric_model_labels,
    _model_attributes,
    _provider_attributes,
    _response_model_attributes,
    _sender_attributes,
    _serialize_conversation_history,
    _session_context_attributes,
    _session_identity_attributes,
    _turn_attributes,
)
from .session import _start_session_span
from .usage import (
    _USAGE_FIELDS,
    _genai_metric_dims,
    _normalize_usage,
    _record_genai_token_usage,
    _record_prompt_cache_metrics,
    _record_usage_metrics,
    _usage_attributes,
)


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
    tracer.sessions.next_turn(session_id, reset=bool(is_first_turn))

    # Capture first LLM input for top-level session span
    if session_id:
        ps = tracer.sessions.get_or_create(session_id)
        if not ps.io_captured:
            ps.io["input"] = _preview_for(tracer, "llm_input", user_message) or ""
            ps.io_captured = True

    # OpenInference attributes — Phoenix Info panel. The provider is only
    # known once an API call has reported it (continuation turns); the
    # platform is not a provider (#153).
    known = tracer.sessions.peek(session_id) if session_id else None
    attributes: Dict[str, Any] = {}
    attributes.update(_session_identity_attributes(session_id))
    attributes.update(_model_attributes(model, known.provider if known else None))
    attributes.update(_gen_ai_attributes(session_id, "chat"))
    attributes.update(_correlation_attributes(tracer, session_id, kwargs))
    attributes.update(_turn_attributes(tracer, session_id))

    if tracer.config.capture_sender_id:
        sender_id = truncate_string(kwargs.get("sender_id"), 200) if kwargs.get("sender_id") else ""
        if sender_id:
            sender_attrs = _sender_attributes(sender_id, truncate_string(platform, 120))
            attributes.update(sender_attrs)
            if session_id:
                ps = tracer.sessions.get_or_create(session_id)
                ps.sender_id = sender_id
                ps.user_id = sender_attrs["user.id"]

    # Opt-in: put the entire conversation the model is about to see on
    # input.value. Falls back to just the latest user_message otherwise —
    # that's the historical default and what small backends handle best.
    full = None
    if tracer.config.capture_conversation_history and tracer.config.capture_previews:
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

    # Capture last LLM output for top-level session span. Only if the
    # session already has I/O buffered (i.e. pre_llm_call ran) — mirrors
    # prior behaviour where we never wrote output without a matching input.
    ps = tracer.sessions.peek(session_id) if session_id else None
    if ps is not None and ps.io_captured:
        ps.io["output"] = preview or ""

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
    attributes.update(_correlation_attributes(tracer, session_id, kwargs))
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


def _request_body(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    request = kwargs.get("request")
    if isinstance(request, dict) and isinstance(request.get("body"), dict):
        return request["body"]
    return {}


def _system_prompt(kwargs: Dict[str, Any], messages: Any, body: Dict[str, Any]) -> Optional[str]:
    """The system prompt: an explicit kwarg, else the leading system message,
    else the Responses API ``instructions`` field."""
    explicit = kwargs.get("system_prompt")
    if explicit:
        return str(explicit)
    if isinstance(messages, list):
        for m in messages:
            if isinstance(m, dict) and m.get("role") in ("system", "developer"):
                content = m.get("content")
                if isinstance(content, list):
                    content = " ".join(
                        str(part.get("text", "")) if isinstance(part, dict) else str(part)
                        for part in content
                    )
                return str(content) if content else None
            break
    instructions = body.get("instructions") or body.get("system")
    return str(instructions) if instructions else None


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

    # Per-turn summary: count api requests
    if session_id:
        tracer.sessions.get_or_create(session_id).turn_summary.api_call_count += 1

    # OpenInference attributes — Phoenix Info panel
    attributes: Dict[str, Any] = {
        "llm.api_mode": api_mode,
        "llm.request.message_count": message_count,
        "llm.request.approx_input_tokens": approx_input_tokens,
    }
    attributes.update(_session_identity_attributes(session_id))
    attributes.update(_model_attributes(model, provider))
    attributes.update(_gen_ai_attributes(session_id, "chat"))
    attributes.update(_gen_ai_request_param_attributes(kwargs))
    attributes.update(_session_context_attributes(tracer, session_id, kwargs))
    if max_tokens:
        attributes["llm.request.max_tokens"] = max_tokens
        attributes["gen_ai.request.max_tokens"] = max_tokens

    if tracer.config.capture_previews and tracer.config.capture_full_prompts:
        # Prefer the raw ``request_messages`` list: it is the exact list sent
        # to the provider and Hermes does not sanitise it. ``request["body"]``
        # is the sanitised view, where every string is capped at 8,000 chars
        # (1,000 once the payload exceeds HERMES_PLUGIN_PAYLOAD_MAX_CHARS) and
        # ends in ``...[truncated N chars]`` — the opposite of full capture.
        messages = kwargs.get("request_messages") or kwargs.get("messages")
        body = _request_body(kwargs)
        if not messages:
            messages = body.get("messages")
        serialized = serialize_full(messages)
        if serialized is not None:
            # One copy per convention (#74): gen_ai.input.messages for the
            # OTel GenAI readers, input.value for OpenInference (Phoenix).
            attributes["gen_ai.input.messages"] = serialized
            attributes["input.value"] = serialized
            attributes["input.mime_type"] = "application/json"
            attributes["hermes.content.input_chars"] = len(serialized)
        system_prompt = _system_prompt(kwargs, messages, body)
        if system_prompt:
            attributes["gen_ai.system_instructions"] = system_prompt

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

    # Build final attributes
    attributes: Dict[str, Any] = {}
    attributes.update(_gen_ai_attributes(session_id, "chat"))
    attributes.update(_provider_attributes(provider))
    # Only a reported response model; the request model is not a response (#155).
    attributes.update(_response_model_attributes(response_model))
    response_id = kwargs.get("response_id") or kwargs.get("id")
    if response_id:
        attributes["gen_ai.response.id"] = truncate_string(response_id, 200)
    attributes.update(_correlation_attributes(tracer, session_id, kwargs))

    model_labels = _metric_model_labels(model, provider)

    # Token usage — dual convention (gen_ai.usage.* + llm.token_count.*).
    # See _usage_attributes for the full attribute list.
    usage = _as_dict(usage)
    if usage:
        totals = _normalize_usage(usage)
        attributes.update(_usage_attributes(totals))

        # Roll up usage to the top-level session/cron span.
        if session_id:
            ps = tracer.sessions.get_or_create(session_id)
            for field in _USAGE_FIELDS:
                ps.usage[field] += totals[field]
            ps.usage_updated = True
            # Remember the real LLM provider and the reported response model —
            # on_session_end only sees the platform and the request model.
            if provider:
                ps.provider = provider
            if response_model:
                ps.response_model = truncate_string(response_model, 200)

        # Record metrics
        _record_usage_metrics(tracer, totals, model_labels)
        _record_prompt_cache_metrics(
            tracer,
            totals,
            usage.get("available_fields"),
            {**model_labels, "api_mode": api_mode},
        )

        # OTel GenAI spec token-usage histogram (dual-write; low cardinality).
        if tracer.config.emit_genai_metrics:
            _record_genai_token_usage(
                tracer,
                "gen_ai.client.token.usage",
                totals,
                _genai_metric_dims(model, provider, response_model),
            )

        cost = usage.get("cost")
        if cost:
            try:
                tracer.record_metric("cost_usage", float(cost), model_labels)
            except (ValueError, TypeError):
                pass

        tracer.record_metric("model_usage", 1, model_labels)

    # Performance metrics
    if api_duration:
        attributes["llm.response.duration_ms"] = round(api_duration * 1000, 1)
        # OTel GenAI spec operation-duration histogram — in SECONDS (the spec
        # unit), unlike the ms hermes.* histograms.
        if tracer.config.emit_genai_metrics:
            tracer.record_metric(
                "gen_ai.client.operation.duration",
                api_duration,
                _genai_metric_dims(model, provider, response_model),
            )
    if finish_reason:
        attributes["llm.response.finish_reason"] = finish_reason
        attributes["gen_ai.response.finish_reasons"] = [finish_reason]
    if assistant_content_chars:
        attributes["llm.response.output_chars"] = assistant_content_chars
    if assistant_tool_call_count:
        attributes["llm.response.tool_calls"] = assistant_tool_call_count

    if tracer.config.capture_previews and tracer.config.capture_full_responses:
        response_content = kwargs.get("response_content")
        response_tool_calls = kwargs.get("response_tool_calls")
        assistant_message = kwargs.get("assistant_message")
        if assistant_message is not None:
            if not response_content:
                response_content = getattr(assistant_message, "content", None)
            if not response_tool_calls:
                response_tool_calls = getattr(assistant_message, "tool_calls", None)
        # One assistant message carrying the text and the tool calls it made,
        # in both conventions, once each (#74).
        message: Dict[str, Any] = {"role": "assistant"}
        if response_content:
            message["content"] = str(response_content)
        tool_calls_serialized = serialize_full(response_tool_calls)
        if tool_calls_serialized is not None:
            try:
                message["tool_calls"] = json.loads(tool_calls_serialized)
            except Exception:
                message["tool_calls"] = tool_calls_serialized
        if len(message) > 1:
            attributes["gen_ai.output.messages"] = json.dumps([message], ensure_ascii=False)
            if response_content:
                attributes["output.value"] = str(response_content)
                attributes["output.mime_type"] = "text/plain"
            else:
                attributes["output.value"] = tool_calls_serialized
                attributes["output.mime_type"] = "application/json"
            attributes["hermes.content.output_chars"] = len(attributes["output.value"] or "")

    # Pop parent
    tracer.spans.pop_parent(session_id=session_id)

    # Mark as OK
    tracer.end_span(key, attributes=attributes, status="ok")
    debug_log(f"  API span ended: status=ok, tokens={usage.get('total_tokens', 0) if usage else 0}")


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
    error = _as_dict(error)
    error_type = truncate_string(error.get("type"), 200) if error.get("type") else ""
    error_message = truncate_string(error.get("message") or reason, 500)
    status_class = http_status_class(status_code)
    is_retryable = coerce_bool(retryable)

    # Build the error attributes that go on whichever span we close.
    attributes: Dict[str, Any] = {}
    if error_type:
        attributes["error.type"] = error_type
    sc = to_optional_int(status_code)
    if sc is not None:
        attributes["http.response.status_code"] = sc
        attributes["gen_ai.response.status_code"] = sc
    rc = to_optional_int(retry_count)
    if rc is not None:
        attributes["hermes.retry.count"] = rc
    mr = to_optional_int(max_retries)
    if mr is not None:
        attributes["hermes.max_retries"] = mr
    if is_retryable is not None:
        attributes["hermes.retryable"] = is_retryable
    if api_duration:
        attributes["llm.response.duration_ms"] = round(api_duration * 1000, 1)
    attributes.update(_gen_ai_attributes(session_id, "chat"))
    attributes.update(_provider_attributes(provider or platform))
    attributes.update(_correlation_attributes(tracer, session_id, kwargs))

    # Remember why this turn failed so on_session_end can surface it on the root.
    if session_id and error_type:
        ps = tracer.sessions.peek(session_id)
        if ps is not None:
            ps.last_error_type = error_type

    key = f"api:{task_id}" if task_id else None
    span = tracer.spans.get_span(key) if key else None
    created_fallback = False

    if span is None:
        # No in-flight api span (error before pre_api_request, or already swept).
        # Create a short-lived span so the failure is still visible. Fail-open.
        fallback_key = key or f"api.error:{kwargs.get('api_request_id') or session_id or 'unknown'}"
        identity: Dict[str, Any] = {}
        identity.update(_session_identity_attributes(session_id))
        identity.update(_model_attributes(model, provider))
        span = tracer.start_span(
            name=f"api.{model}" if model else "api.error",
            key=fallback_key,
            kind="llm",
            attributes=identity,
            session_id=session_id,
        )
        key = fallback_key
        created_fallback = True

    # Record an OTel exception event (semconv: an event named "exception").
    if span is not None and hasattr(span, "add_event"):
        event_attrs: Dict[str, Any] = {
            "exception.type": error_type or "error",
            "exception.escaped": True,
        }
        if error_message:
            event_attrs["exception.message"] = error_message
        try:
            span.add_event("exception", event_attrs)
        except Exception:  # pragma: no cover — never let telemetry raise
            pass

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

    # OTel GenAI spec operation-duration on the failure path, tagged with
    # error.type so success/error durations are queryable from one histogram.
    if api_duration and tracer.config.emit_genai_metrics:
        duration_dims = _genai_metric_dims(model, provider)
        duration_dims["error.type"] = error_type or "unknown"
        tracer.record_metric("gen_ai.client.operation.duration", api_duration, duration_dims)

    debug_log(f"  API error span ended: key={key}, error.type={error_type}, class={status_class}")
