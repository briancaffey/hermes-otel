"""Hermes OTel plugin — hook callbacks.

Each hook starts or ends a span, passing data through to OTel attributes.
"""

from __future__ import annotations

import json
import time
from typing import Any

try:
    from .debug_utils import debug_log
    from .tracer import get_tracer
except ImportError:  # pragma: no cover - flat-module fallback for packaging
    from debug_utils import debug_log
    from tracer import get_tracer

debug_log("hooks.py module loaded")

# Per-session token aggregation for top-level session/cron spans.
_SESSION_USAGE: dict[str, dict[str, int]] = {}

# Per-session I/O for top-level agent span in Phoenix.
_SESSION_IO: dict[str, dict[str, str]] = {}

# Track tool start times for duration calculation.
_TOOL_START_TIMES: dict[str, float] = {}


def _safe_str(value: Any, max_len: int = 1000) -> str:
    """Safely convert to string, truncating if needed."""
    try:
        text = str(value)
    except Exception:
        text = "<unserializable>"
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _to_int(value: Any) -> int:
    """Best-effort integer conversion for usage counters."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return int(float(text))
        except ValueError:
            return 0
    return 0


def _detect_session_kind(platform: str, kwargs: dict) -> str:
    """Determine session type from explicit fields or fallback to detection."""
    session_type = kwargs.get("session_type")
    if session_type:
        return session_type

    origin = kwargs.get("origin")
    if origin:
        return origin

    run_type = kwargs.get("run_type")
    if run_type:
        return run_type

    for candidate in [platform, kwargs.get("source"), kwargs.get("trigger")]:
        if candidate and "cron" in str(candidate).lower():
            return "cron"

    if kwargs.get("job_id") or kwargs.get("cron_job_id"):
        return "cron"

    return "session"


def on_session_start(session_id: str, model: str, platform: str, **kwargs):
    """Start a top-level session span (or cron span) for the entire run."""
    tracer = get_tracer()
    debug_log(f"on_session_start fired: session={session_id}, platform={platform}")
    if not tracer.is_enabled:
        return

    kind = _detect_session_kind(platform, kwargs)
    span_name = "agent" if kind != "cron" else "cron"
    key = f"session:{session_id}"

    attributes = {
        "session_id": _safe_str(session_id, 200),
        "hermes.session_id": _safe_str(session_id, 120),
        "hermes.session.kind": kind,
        "llm.model_name": _safe_str(model, 200),
        "llm.provider": _safe_str(platform, 120),
    }

    tracer.record_metric("session_count", 1, {"session_id": session_id})

    cron_job_id = kwargs.get("job_id") or kwargs.get("cron_job_id")
    if cron_job_id:
        attributes["hermes.cron.job_id"] = _safe_str(cron_job_id, 200)

    span = tracer.start_span(
        name=span_name,
        key=key,
        kind="agent",
        attributes=attributes,
    )
    tracer.spans.push_parent(span)
    debug_log(f"  session span started: key={key}, name={span_name}")


def on_session_end(session_id: str, completed: bool, interrupted: bool, model: str, platform: str, **kwargs):
    """Close the top-level session span."""
    tracer = get_tracer()
    debug_log(
        f"on_session_end fired: session={session_id}, completed={completed}, interrupted={interrupted}"
    )
    if not tracer.is_enabled:
        return

    key = f"session:{session_id}"
    attributes = {
        "hermes.session.completed": bool(completed),
        "hermes.session.interrupted": bool(interrupted),
        "llm.model_name": _safe_str(model, 200),
        "llm.provider": _safe_str(platform, 120),
    }

    # Add first input / last output for Phoenix top-level span display
    session_io = _SESSION_IO.pop(session_id, None)
    if session_io:
        if session_io.get("input"):
            attributes["input.value"] = session_io["input"]
        if session_io.get("output"):
            attributes["output.value"] = session_io["output"]

    usage_totals = _SESSION_USAGE.pop(session_id, None)
    if usage_totals:
        attributes["llm.token_count.prompt"] = usage_totals.get("prompt_tokens", 0)
        attributes["llm.token_count.completion"] = usage_totals.get("completion_tokens", 0)
        attributes["llm.token_count.total"] = usage_totals.get("total_tokens", 0)
        attributes["gen_ai.usage.input_tokens"] = usage_totals.get("prompt_tokens", 0)
        attributes["gen_ai.usage.output_tokens"] = usage_totals.get("completion_tokens", 0)
        attributes["gen_ai.usage.total_tokens"] = usage_totals.get("total_tokens", 0)
        if usage_totals.get("cache_read_tokens", 0):
            attributes["llm.token_count.cache_read"] = usage_totals["cache_read_tokens"]
            attributes["gen_ai.usage.cache_read_input_tokens"] = usage_totals["cache_read_tokens"]
        if usage_totals.get("cache_write_tokens", 0):
            attributes["llm.token_count.cache_write"] = usage_totals["cache_write_tokens"]
            attributes["gen_ai.usage.cache_creation_input_tokens"] = usage_totals["cache_write_tokens"]

    status = "ok" if completed or interrupted else "error"

    tracer.spans.pop_parent()
    tracer.end_span(key, attributes=attributes, status=status)
    debug_log(f"  session span ended: key={key}, status={status}")


def on_pre_tool_call(tool_name: str, args: dict, task_id: str, **kwargs):
    """Start a tool span before the tool executes."""
    debug_log(f"pre_tool_call fired: tool={tool_name}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return

    key = f"{tool_name}:{task_id}"
    _TOOL_START_TIMES[key] = time.perf_counter()

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

    start_time = _TOOL_START_TIMES.pop(key, None)
    if start_time:
        duration_ms = (time.perf_counter() - start_time) * 1000
        tracer.record_metric("tool_duration", duration_ms, {"tool_name": tool_name})

    # Build final attributes — OpenInference conventions for Phoenix Info
    attributes = {}

    # Check if result indicates an error (only if explicit error present)
    has_error = False
    error_msg = ""
    if isinstance(result, dict):
        result_json = result
    else:
        try:
            result_json = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            result_json = {}

    # Only mark as error if there's a non-empty error field
    if isinstance(result_json, dict):
        error_val = result_json.get("error")
        if error_val and str(error_val).strip():
            has_error = True
            error_msg = _safe_str(error_val, 500)
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

    # Capture first LLM input for top-level session span
    if session_id not in _SESSION_IO:
        _SESSION_IO[session_id] = {"input": _safe_str(user_message, 500), "output": ""}

    # OpenInference attributes — Phoenix Info panel
    attributes = {
        "session_id": _safe_str(session_id, 200),
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

    # Capture last LLM output for top-level session span
    if session_id in _SESSION_IO:
        _SESSION_IO[session_id]["output"] = _safe_str(assistant_response, 500)

    tracer.record_metric("message_count", 1, {"session_id": session_id, "model": model, "provider": platform})

    # OpenInference attributes — Phoenix Info panel
    attributes = {
        "session_id": _safe_str(session_id, 200),
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
    debug_log(f"pre_api_request fired: model={model}, provider={provider}, session={session_id}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return

    key = f"api:{task_id}"

    # OpenInference attributes — Phoenix Info panel
    attributes = {
        "session_id": _safe_str(session_id, 200),
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
        completion_tokens = _to_int(usage.get("output_tokens") or usage.get("completion_tokens", 0))
        prompt_tokens = _to_int(usage.get("prompt_tokens") or usage.get("input_tokens", 0))
        total_tokens = _to_int(usage.get("total_tokens", 0)) or (prompt_tokens + completion_tokens)

        # Langfuse / OTel GenAI semantic conventions
        attributes["gen_ai.usage.input_tokens"] = prompt_tokens
        attributes["gen_ai.usage.output_tokens"] = completion_tokens
        attributes["gen_ai.usage.total_tokens"] = total_tokens

        # Phoenix / OpenInference conventions
        attributes["llm.token_count.prompt"] = prompt_tokens
        attributes["llm.token_count.completion"] = completion_tokens
        attributes["llm.token_count.total"] = total_tokens

        # Cache tokens if available
        cache_read = _to_int(usage.get("cache_read_tokens"))
        cache_write = _to_int(usage.get("cache_write_tokens"))
        if cache_read:
            attributes["llm.token_count.cache_read"] = cache_read
            attributes["gen_ai.usage.cache_read_input_tokens"] = cache_read
        if cache_write:
            attributes["llm.token_count.cache_write"] = cache_write
            attributes["gen_ai.usage.cache_creation_input_tokens"] = cache_write

        # Roll up usage to the top-level session/cron span.
        if session_id:
            totals = _SESSION_USAGE.setdefault(
                session_id,
                {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                },
            )
            totals["prompt_tokens"] += prompt_tokens
            totals["completion_tokens"] += completion_tokens
            totals["total_tokens"] += total_tokens
            totals["cache_read_tokens"] += cache_read
            totals["cache_write_tokens"] += cache_write

        # Record metrics
        metric_attrs = {"model": model, "provider": provider}
        if session_id:
            metric_attrs["session_id"] = session_id

        if prompt_tokens:
            tracer.record_metric("token_usage", prompt_tokens, {**metric_attrs, "token_type": "input"})
        if completion_tokens:
            tracer.record_metric("token_usage", completion_tokens, {**metric_attrs, "token_type": "output"})
        if cache_read:
            tracer.record_metric("token_usage", cache_read, {**metric_attrs, "token_type": "cacheRead"})
        if cache_write:
            tracer.record_metric("token_usage", cache_write, {**metric_attrs, "token_type": "cacheCreation"})

        cost = usage.get("cost") if usage else None
        if cost:
            try:
                tracer.record_metric("cost_usage", float(cost), metric_attrs)
            except (ValueError, TypeError):
                pass

        tracer.record_metric("model_usage", 1, {"model": model, "provider": provider})

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
