"""One provider attempt, shared by every hook that records one (#405).

The main loop's ``pre/post_api_request``, the auxiliary ``pre/post_auxiliary_call``
and ``api_request_error`` all describe the same thing: an ``api.<model>`` span
with the request's shape, the response's usage, cost and timing, and a fallback
span when the end arrives without a start. Each used to carry its own copy of
that; the copies live here once. Everything here is attribute and metric
*building*: the hooks keep the span lifecycle (start, push, pop, end), the
session roll-ups and the events, which differ per hook.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, Dict, Mapping, Optional, Tuple

from ..helpers import serialize_full
from ..semconv import QUERY_SOURCE_MAIN, QUERY_SOURCE_SUBAGENT
from ._common import as_dict
from .attributes import (
    _gen_ai_attributes,
    _gen_ai_request_param_attributes,
    _model_attributes,
    _session_context_attributes,
    _session_identity_attributes,
)
from .usage import (
    _estimate_cost,
    _record_genai_token_usage,
    _record_prompt_cache_metrics,
    _record_usage_metrics,
    genai_metric_dims,
    normalize_usage,
    usage_attributes,
)


def query_source_for(is_subagent: Any) -> str:
    """``main`` or ``subagent`` for a main-loop API call."""
    return QUERY_SOURCE_SUBAGENT if is_subagent else QUERY_SOURCE_MAIN


class HookPayload:
    """The ``**kwargs`` of an api hook with every alternate spelling read in one place.

    Hermes has sent the request messages as ``request_messages`` and as
    ``messages`` (and inside ``request["body"]``), the response id as
    ``response_id`` and as ``id``, the assistant's reply as the raw
    ``assistant_message`` object and inside ``response``; the hooks no longer
    spell those fallbacks themselves.
    """

    def __init__(self, kwargs: Mapping[str, Any]) -> None:
        self.kwargs = kwargs

    def get(self, key: str, default: Any = None) -> Any:
        return self.kwargs.get(key, default)

    @property
    def body(self) -> Dict[str, Any]:
        """``request["body"]``: Hermes's sanitised view of the request (strings capped)."""
        request = self.kwargs.get("request")
        if isinstance(request, dict) and isinstance(request.get("body"), dict):
            return request["body"]
        return {}

    @property
    def messages(self) -> Any:
        """The exact list sent to the provider: ``request_messages`` (unsanitised)
        first, then ``messages``, then the sanitised ``request["body"]["messages"]``."""
        messages = self.kwargs.get("request_messages") or self.kwargs.get("messages")
        if not messages:
            messages = self.body.get("messages")
        return messages

    @property
    def response_id(self) -> Any:
        return self.kwargs.get("response_id") or self.kwargs.get("id")

    @property
    def assistant_message(self) -> Tuple[Any, Any]:
        """``(content, tool_calls)`` of the reply, from whichever spelling Hermes
        used: the raw ``assistant_message`` object (``post_api_request``), else
        the sanitised ``response["assistant_message"]`` dict (both hooks)."""
        message = self.kwargs.get("assistant_message")
        if message is None:
            message = as_dict(self.kwargs.get("response")).get("assistant_message")
        if message is None:
            return None, None
        return _field(message, "content"), _field(message, "tool_calls")

    def system_prompt(self, messages: Any) -> Optional[str]:
        """The system prompt: an explicit kwarg, else the leading system message,
        else the Responses API ``instructions`` field."""
        explicit = self.kwargs.get("system_prompt")
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
        body = self.body
        instructions = body.get("instructions") or body.get("system")
        return str(instructions) if instructions else None


def _field(message: Any, name: str) -> Any:
    """A field of a message Hermes sent as a dict or as an SDK object."""
    return message.get(name) if isinstance(message, dict) else getattr(message, name, None)


# ── the request side ───────────────────────────────────────────────────


def request_attributes(
    tracer,
    payload: HookPayload,
    *,
    session_id: Optional[str],
    model: Any,
    provider: Any,
    message_count: Any,
    approx_input_tokens: Any,
    max_tokens: Any,
) -> Dict[str, Any]:
    """The attributes every ``api.<model>`` span starts with: the request's
    size, the session and model identity in both conventions, the GenAI
    request parameters, the session context, and (in full capture) the
    complete prompt. Hook-specific keys (``llm.api_mode``, the query source,
    the auxiliary task) are added by the caller."""
    attributes: Dict[str, Any] = {
        "llm.request.message_count": message_count,
        "llm.request.approx_input_tokens": approx_input_tokens,
    }
    attributes.update(_session_identity_attributes(session_id))
    attributes.update(_model_attributes(model, provider))
    attributes.update(_gen_ai_attributes(session_id, "chat"))
    attributes.update(_gen_ai_request_param_attributes(payload.kwargs))
    attributes.update(_session_context_attributes(tracer, session_id, payload.kwargs))
    if max_tokens:
        attributes["llm.request.max_tokens"] = max_tokens
        attributes["gen_ai.request.max_tokens"] = max_tokens
    attributes.update(full_prompt_attributes(tracer, payload))
    return attributes


def full_prompt_attributes(tracer, payload: HookPayload) -> Dict[str, Any]:
    """The complete prompt in both conventions (#74), only in full capture.

    Prefers the raw ``request_messages`` list: it is the exact list sent to
    the provider and Hermes does not sanitise it. ``request["body"]`` is the
    sanitised view, where every string is capped at 8,000 chars (1,000 once
    the payload exceeds HERMES_PLUGIN_PAYLOAD_MAX_CHARS) and ends in
    ``...[truncated N chars]``, the opposite of full capture.
    """
    cfg = tracer.config
    if not cfg.captures_full_prompts():
        return {}
    attributes: Dict[str, Any] = {}
    messages = payload.messages
    serialized = serialize_full(messages)
    if serialized is not None:
        # One copy per convention: gen_ai.input.messages for the OTel GenAI
        # readers, input.value for OpenInference (Phoenix).
        attributes["gen_ai.input.messages"] = serialized
        attributes["input.value"] = serialized
        attributes["input.mime_type"] = "application/json"
        attributes["hermes.content.input_chars"] = len(serialized)
    system_prompt = payload.system_prompt(messages)
    if system_prompt:
        attributes["gen_ai.system_instructions"] = system_prompt
    return attributes


# ── the response side ──────────────────────────────────────────────────


def apply_usage(
    tracer,
    usage: Dict[str, Any],
    attributes: Dict[str, Any],
    *,
    model: Any,
    provider: Any,
    base_url: Any,
    response_model: Any,
    model_labels: Dict[str, Any],
    cache_labels: Dict[str, Any],
) -> Tuple[Optional[Dict[str, int]], Optional[Dict[str, Any]]]:
    """Token usage and cost for one attempt: the dual-convention usage
    attributes, the ``hermes.token.usage`` / prompt-cache counters and the
    GenAI token histogram, the cost estimate (#252) as attributes and the ``cost_usage``
    metric, and one ``model_usage`` count. Returns ``(totals, cost)``;
    ``(None, None)`` when the attempt reported no usage.

    ``cache_labels`` is the label set for the prompt-cache metrics: the main
    loop sends its query source with them, the auxiliary hook does not.
    """
    if not usage:
        return None, None
    totals = normalize_usage(usage)
    attributes.update(usage_attributes(totals))
    _record_usage_metrics(tracer, totals, model_labels)
    _record_prompt_cache_metrics(tracer, totals, usage.get("available_fields"), cache_labels)
    # OTel GenAI spec token-usage histogram (dual-write; low cardinality).
    _record_genai_token_usage(
        tracer,
        "gen_ai.client.token.usage",
        totals,
        genai_metric_dims(model, provider, response_model),
    )
    # Hermes's own estimate for this call. An unknown price or a
    # subscription-included route carries a status but no amount, so a
    # missing price never reads as $0.
    cost = _estimate_cost(model, provider, base_url, usage)
    if cost is not None:
        attributes["hermes.cost.status"] = cost["status"]
        if cost["source"]:
            attributes["hermes.cost.source"] = cost["source"]
        if cost["usd"] is not None:
            attributes["hermes.cost.usage"] = cost["usd"]
            tracer.record_metric(
                "cost_usage", cost["usd"], {**model_labels, "cost_status": cost["status"]}
            )
            # Claude Code's name for the same figure (#485); dropped unless
            # metric_name_compat switches it on.
            tracer.record_metric("claude_code.cost.usage", cost["usd"], model_labels)
    tracer.record_metric("model_usage", 1, model_labels)
    return totals, cost


def duration_seconds(value: Any) -> Optional[float]:
    """``api_duration`` as a float, or None when absent or unusable."""
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def response_attributes(
    *,
    api_duration: Optional[float],
    finish_reason: Any = None,
    assistant_content_chars: Any = None,
    assistant_tool_call_count: Any = None,
) -> Dict[str, Any]:
    """Timing, finish reason and response size in both conventions (the
    failure path has only the timing)."""
    attributes: Dict[str, Any] = {}
    if api_duration:
        attributes["llm.response.duration_ms"] = round(api_duration * 1000, 1)
    if finish_reason:
        attributes["llm.response.finish_reason"] = finish_reason
        attributes["gen_ai.response.finish_reasons"] = [finish_reason]
    if assistant_content_chars:
        attributes["llm.response.output_chars"] = assistant_content_chars
    if assistant_tool_call_count:
        attributes["llm.response.tool_calls"] = assistant_tool_call_count
    return attributes


def full_response_attributes(tracer, payload: HookPayload) -> Dict[str, Any]:
    """The complete response in both conventions (#74), only in full capture:
    one assistant message carrying the text and the tool calls it made."""
    if not tracer.config.captures_full_responses():
        return {}
    content, tool_calls = payload.assistant_message
    message: Dict[str, Any] = {"role": "assistant"}
    if content:
        message["content"] = str(content)
    tool_calls_serialized = serialize_full(tool_calls)
    if tool_calls_serialized is not None:
        try:
            message["tool_calls"] = json.loads(tool_calls_serialized)
        except Exception:
            message["tool_calls"] = tool_calls_serialized
    if len(message) == 1:
        return {}
    attributes: Dict[str, Any] = {
        "gen_ai.output.messages": json.dumps([message], ensure_ascii=False)
    }
    if content:
        attributes["output.value"] = str(content)
        attributes["output.mime_type"] = "text/plain"
    else:
        attributes["output.value"] = tool_calls_serialized
        attributes["output.mime_type"] = "application/json"
    attributes["hermes.content.output_chars"] = len(attributes["output.value"] or "")
    return attributes


def first_chunk_seconds(started_at: Any, first_chunk_at: Any) -> Optional[float]:
    """Seconds from the call's start to its first stream chunk (#396).

    Hermes passes both as wall-clock ``time.time()`` values on
    ``post_api_request`` (``first_chunk_at`` only for a streamed response);
    ``started_at`` is taken before any retry, so this includes retries.
    None when either is missing or the difference is negative.
    """
    try:
        if started_at is None or first_chunk_at is None:
            return None
        s, f = float(started_at), float(first_chunk_at)
    except (TypeError, ValueError):
        return None
    if f < s:
        return None
    return f - s


def record_first_chunk(
    tracer, attributes: Dict[str, Any], payload: Any, labels: Dict[str, Any]
) -> None:
    """``hermes.api.ttfc_ms`` on the span and the time-to-first-chunk histogram."""
    seconds = first_chunk_seconds(payload.get("started_at"), payload.get("first_chunk_at"))
    if seconds is None:
        return
    attributes["hermes.api.ttfc_ms"] = round(seconds * 1000, 1)
    tracer.record_metric("api_time_to_first_chunk", seconds, labels)


def record_operation_duration(
    tracer, api_duration: Optional[float], dims: Dict[str, Any], error_type: Optional[str] = None
) -> None:
    """The OTel GenAI operation-duration histogram, in SECONDS (the spec
    unit), tagged with ``error.type`` on the failure path so success and
    error durations are queryable from one histogram."""
    if not api_duration:
        return
    if error_type is not None:
        dims = dict(dims)
        dims["error.type"] = error_type
    tracer.record_metric("gen_ai.client.operation.duration", api_duration, dims)


def token_suffix(attributes: Mapping[str, Any]) -> str:
    """`` (in/out tokens)`` for an event body, empty when neither count is known."""
    in_tok = attributes.get("gen_ai.usage.input_tokens")
    out_tok = attributes.get("gen_ai.usage.output_tokens")
    if in_tok is None and out_tok is None:
        return ""
    return f" ({in_tok}/{out_tok} tokens)"


# ── spans that end without a start ─────────────────────────────────────


def ensure_span(
    tracer,
    key: str,
    *,
    name: str,
    session_id: Optional[str],
    model: Any,
    provider: Any,
    links: Optional[list] = None,
) -> Tuple[Any, bool]:
    """The in-flight span for ``key``, or a fresh one carrying the identity
    the start hook would have set. Returns ``(span, created)``.

    An end hook can arrive without its start (the start fired before the
    plugin loaded, the attempt outlived the orphan sweep, an error came
    before ``pre_api_request``); the failure or the spend is still recorded
    on a short-lived span rather than lost.
    """
    span = tracer.spans.get_span(key)
    if span is not None:
        return span, False
    identity: Dict[str, Any] = {}
    identity.update(_session_identity_attributes(session_id))
    identity.update(_model_attributes(model, provider))
    span = tracer.start_span(
        name=name, key=key, kind="llm", attributes=identity, session_id=session_id, links=links
    )
    return span, True


def add_exception_event(
    span: Any, error_type: str, error_message: Optional[str], *, keep_empty_message: bool = False
) -> None:
    """The OTel semconv ``exception`` event on a span; never raises."""
    if span is None or not hasattr(span, "add_event"):
        return
    attrs: Dict[str, Any] = {"exception.type": error_type, "exception.escaped": True}
    if error_message or keep_empty_message:
        attrs["exception.message"] = error_message or ""
    with contextlib.suppress(Exception):  # never let telemetry raise
        span.add_event("exception", attrs)
