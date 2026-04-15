"""Hermes OTel plugin — hook callbacks.

Each hook starts or ends a span, passing data through to OTel attributes.
"""

from __future__ import annotations

import json
from typing import Any

try:
    from .debug_utils import debug_log
    from .tracer import get_tracer
except ImportError:  # pragma: no cover - flat-module fallback for packaging
    from debug_utils import debug_log
    from tracer import get_tracer

debug_log("hooks.py module loaded")


def _safe_str(value: Any, max_len: int = 1000) -> str:
    """Safely convert to string, truncating if needed."""
    try:
        text = str(value)
    except Exception:
        text = "<unserializable>"
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def on_pre_tool_call(tool_name: str, args: dict, task_id: str, **kwargs):
    """Start a tool span before the tool executes."""
    debug_log(f"pre_tool_call fired: tool={tool_name}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return

    key = f"{tool_name}:{task_id}"

    # OpenInference attributes — Phoenix Info panel
    attributes = {
        "tool.name": tool_name,
        "input.value": _safe_str(json.dumps(args) if args else "{}", 500),
    }

    span = tracer.start_span(
        name=f"tool.{tool_name}",
        key=key,
        kind="tool",
        attributes=attributes,
    )
    debug_log(f"  span created: key={key}")


def on_post_tool_call(tool_name: str, args: dict, result: str, task_id: str, **kwargs):
    """End the tool span and record the result."""
    debug_log(f"post_tool_call fired: tool={tool_name}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return

    key = f"{tool_name}:{task_id}"
    debug_log(f"  ending span: key={key}")

    # Build final attributes — OpenInference conventions for Phoenix Info
    attributes = {}

    # Check if result indicates an error
    has_error = False
    error_msg = ""
    if isinstance(result, dict):
        result_json = result
    else:
        try:
            result_json = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            result_json = {}

    if isinstance(result_json, dict) and "error" in result_json:
        has_error = True
        error_msg = _safe_str(result_json["error"], 500)
        attributes["error.message"] = error_msg

    # OpenInference output value — Phoenix shows this in Info
    attributes["output.value"] = _safe_str(result, 2000)

    # Set status based on success/error
    status = "error" if has_error else "ok"
    tracer.end_span(key, attributes=attributes, status=status, error_message=error_msg if has_error else None)
    debug_log(f"  span ended: status={status}")


def on_pre_llm_call(session_id: str, user_message: str, conversation_history: list,
                    is_first_turn: bool, model: str, platform: str, **kwargs):
    """Start an LLM span before the model is called."""
    debug_log(f"pre_llm_call fired: model={model}, session={session_id}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return None

    key = f"llm:{session_id}"

    # OpenInference attributes — Phoenix Info panel
    attributes = {
        "llm.model_name": model,
        "llm.provider": platform,
        "input.value": _safe_str(user_message, 500),
    }

    span = tracer.start_span(
        name=f"llm.{model}",
        key=key,
        kind="llm",
        attributes=attributes,
    )

    # Push as parent — tool spans during this LLM call will nest under it
    tracer.spans.push_parent(span)
    debug_log(f"  LLM span started: key={key}")
    return None  # Don't inject context, just observe


def on_post_llm_call(session_id: str, user_message: str, assistant_response: str,
                     conversation_history: list, model: str, platform: str, **kwargs):
    """End the LLM span and record the response."""
    debug_log(f"post_llm_call fired: model={model}, session={session_id}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return

    key = f"llm:{session_id}"
    debug_log(f"  ending span: key={key}")

    # OpenInference attributes — Phoenix Info panel
    attributes = {
        "output.value": _safe_str(assistant_response, 500),
    }

    # Pop parent — tool spans after this won't nest under this LLM call
    tracer.spans.pop_parent()

    # Mark as OK — LLM call completed successfully
    tracer.end_span(key, attributes=attributes, status="ok")
    debug_log(f"  LLM span ended: status=ok")


def on_pre_api_request(task_id: str, session_id: str, platform: str, model: str,
                       provider: str, base_url: str, api_mode: str, api_call_count: int,
                       message_count: int, tool_count: int, approx_input_tokens: int,
                       request_char_count: int, max_tokens: int, **kwargs):
    """Fires before each individual LLM API request."""
    debug_log(f"pre_api_request fired: model={model}, provider={provider}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return

    key = f"api:{task_id}"

    # OpenInference attributes — Phoenix Info panel
    attributes = {
        "llm.model_name": model,
        "llm.provider": provider,
        "llm.api_mode": api_mode,
        "llm.request.message_count": message_count,
        "llm.request.approx_input_tokens": approx_input_tokens,
    }
    if max_tokens:
        attributes["llm.request.max_tokens"] = max_tokens

    span = tracer.start_span(
        name=f"api.{model}",
        key=key,
        kind="llm",
        attributes=attributes,
    )

    # Push as parent — tool spans during this API call will nest under it
    tracer.spans.push_parent(span)
    debug_log(f"  API span started: key={key}")


def on_post_api_request(task_id: str, session_id: str, platform: str, model: str,
                        provider: str, base_url: str, api_mode: str, api_call_count: int,
                        api_duration: float, finish_reason: str, message_count: int,
                        response_model: str, usage: dict, assistant_content_chars: int,
                        assistant_tool_call_count: int, **kwargs):
    """Fires after each individual LLM API request with usage stats."""
    debug_log(f"post_api_request fired: model={model}, finish={finish_reason}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return

    key = f"api:{task_id}"
    debug_log(f"  ending span: key={key}, usage={usage}")

    # Build final attributes
    attributes = {}

    # Token usage — dual convention:
    #   gen_ai.usage.*  → OTel standard, Langfuse recognizes these
    #   llm.token_count.* → OpenInference, Phoenix recognizes these
    if usage:
        # Hermes uses 'output_tokens', some providers use 'completion_tokens'
        completion_tokens = usage.get("output_tokens") or usage.get("completion_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens", 0)

        # Langfuse / OTel GenAI semantic conventions
        attributes["gen_ai.usage.input_tokens"] = prompt_tokens
        attributes["gen_ai.usage.output_tokens"] = completion_tokens
        attributes["gen_ai.usage.total_tokens"] = usage.get("total_tokens", 0)

        # Phoenix / OpenInference conventions
        attributes["llm.token_count.prompt"] = prompt_tokens
        attributes["llm.token_count.completion"] = completion_tokens
        attributes["llm.token_count.total"] = usage.get("total_tokens", 0)

        # Cache tokens if available
        if usage.get("cache_read_tokens"):
            attributes["llm.token_count.cache_read"] = usage["cache_read_tokens"]
            attributes["gen_ai.usage.cache_read_input_tokens"] = usage["cache_read_tokens"]
        if usage.get("cache_write_tokens"):
            attributes["llm.token_count.cache_write"] = usage["cache_write_tokens"]
            attributes["gen_ai.usage.cache_creation_input_tokens"] = usage["cache_write_tokens"]

    # Performance metrics
    if api_duration:
        attributes["llm.response.duration_ms"] = round(api_duration * 1000, 1)
    if finish_reason:
        attributes["llm.response.finish_reason"] = finish_reason
    if assistant_content_chars:
        attributes["llm.response.output_chars"] = assistant_content_chars
    if assistant_tool_call_count:
        attributes["llm.response.tool_calls"] = assistant_tool_call_count

    # Pop parent
    tracer.spans.pop_parent()

    # Mark as OK
    tracer.end_span(key, attributes=attributes, status="ok")
    debug_log(f"  API span ended: status=ok, tokens={usage.get('total_tokens', 0) if usage else 0}")
