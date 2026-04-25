"""Hermes OTel plugin — hook callbacks.

Each hook starts or ends a span, passing data through to OTel attributes.

Per-session buffering (token totals, first input / last output, per-turn
summary, tool start times) lives on ``tracer.sessions`` — see
``session_state.py``. Nothing in this module holds state; everything is
routed through the tracer singleton so test reset is just
``get_tracer()`` re-creation.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, TypedDict

from .debug_utils import debug_log
from .helpers import (
    clip_preview,
    extract_tool_result_status,
    infer_skill_name,
    resolve_tool_identity,
    truncate_string,
)
from .session_state import TurnSummary
from .tracer import get_tracer


class HookContext(TypedDict, total=False):
    """Optional extras Hermes may pass through a hook's ``**kwargs``.

    All fields are optional (``total=False``); hooks guard with
    ``kwargs.get(...)``. Documented here so new contributors can see
    what's available without reading hermes-agent internals.

    ``session_id`` is passed to the tool / api hooks (whose fixed
    signatures don't already take one) so per-session state lands in
    the right bucket. The remaining fields feed
    :func:`_detect_session_kind` to classify a run as ``"session"``,
    ``"cron"``, or a custom value from the host app.
    """

    # Bucketing for per-session aggregation.
    session_id: str

    # Session-kind classification — first non-empty field wins, so listing
    # them here matches the precedence in _detect_session_kind.
    session_type: str
    origin: str
    run_type: str
    source: str
    trigger: str
    job_id: str
    cron_job_id: str


_MAX_SUMMARY_CHARS = 500


def _clip_joined(items: List[str], sep: str, limit: int = _MAX_SUMMARY_CHARS) -> str:
    """Join items with separator, capped to `limit` chars with '...' suffix."""
    if not items:
        return ""
    joined = sep.join(items)
    if len(joined) <= limit:
        return joined
    if limit <= 3:
        return "." * limit
    return joined[: limit - 3] + "..."


def _summary_attributes(summary: TurnSummary) -> Dict[str, Any]:
    """Convert a TurnSummary into hermes.turn.* attribute dict."""
    attrs: Dict[str, Any] = {}
    if summary.tool_names:
        attrs["hermes.turn.tool_count"] = len(summary.tool_names)
        attrs["hermes.turn.tools"] = _clip_joined(sorted(summary.tool_names), ",")
    if summary.tool_targets:
        attrs["hermes.turn.tool_targets"] = _clip_joined(summary.tool_targets, "|")
    if summary.tool_commands:
        attrs["hermes.turn.tool_commands"] = _clip_joined(summary.tool_commands, "|")
    if summary.tool_outcomes:
        attrs["hermes.turn.tool_outcomes"] = _clip_joined(sorted(summary.tool_outcomes), ",")
    if summary.skill_names:
        attrs["hermes.turn.skill_count"] = len(summary.skill_names)
        attrs["hermes.turn.skills"] = _clip_joined(sorted(summary.skill_names), ",")
    if summary.api_call_count:
        attrs["hermes.turn.api_call_count"] = summary.api_call_count
    if summary.final_status:
        attrs["hermes.turn.final_status"] = summary.final_status
    return attrs


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


# Canonical token-total field order. Used when iterating or copying.
_USAGE_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


def _normalize_usage(usage: dict) -> Dict[str, int]:
    """Parse a raw hermes ``usage`` dict into canonical token totals.

    Hermes exposes ``output_tokens``; some providers use ``completion_tokens``.
    Similarly ``input_tokens`` vs ``prompt_tokens``. Total is derived from
    the reported value or sum(prompt, completion) when absent. Returns all
    five canonical fields, zero-filled.
    """
    completion = _to_int(usage.get("output_tokens") or usage.get("completion_tokens", 0))
    prompt = _to_int(usage.get("prompt_tokens") or usage.get("input_tokens", 0))
    total = _to_int(usage.get("total_tokens", 0)) or (prompt + completion)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cache_read_tokens": _to_int(usage.get("cache_read_tokens")),
        "cache_write_tokens": _to_int(usage.get("cache_write_tokens")),
    }


def _usage_attributes(totals: Dict[str, int]) -> Dict[str, Any]:
    """Build dual-convention OTel attributes from canonical token totals.

    Emits both the OTel GenAI convention (``gen_ai.usage.*`` — recognised
    by Langfuse) and the OpenInference convention (``llm.token_count.*``
    — recognised by Phoenix). Cache attrs are included only when non-zero
    so low-traffic spans don't get cluttered with zero fields.
    """
    prompt = totals["prompt_tokens"]
    completion = totals["completion_tokens"]
    total = totals["total_tokens"]
    cache_read = totals["cache_read_tokens"]
    cache_write = totals["cache_write_tokens"]

    attrs: Dict[str, Any] = {
        # OpenInference (Phoenix)
        "llm.token_count.prompt": prompt,
        "llm.token_count.completion": completion,
        "llm.token_count.total": total,
        # OTel GenAI (Langfuse)
        "gen_ai.usage.input_tokens": prompt,
        "gen_ai.usage.output_tokens": completion,
        "gen_ai.usage.total_tokens": total,
    }
    if cache_read:
        attrs["llm.token_count.prompt_details.cache_read"] = cache_read
        attrs["gen_ai.usage.cache_read_input_tokens"] = cache_read
    if cache_write:
        attrs["llm.token_count.prompt_details.cache_write"] = cache_write
        attrs["gen_ai.usage.cache_creation_input_tokens"] = cache_write
    return attrs


_USAGE_METRIC_LABELS = (
    ("prompt_tokens", "input"),
    ("completion_tokens", "output"),
    ("cache_read_tokens", "cacheRead"),
    ("cache_write_tokens", "cacheCreation"),
)


def _record_usage_metrics(tracer, totals: Dict[str, int], base_attrs: Dict[str, Any]) -> None:
    """Record one ``token_usage`` metric per non-zero canonical field."""
    for key, label in _USAGE_METRIC_LABELS:
        v = totals.get(key, 0)
        if v:
            tracer.record_metric("token_usage", v, {**base_attrs, "token_type": label})


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


def _preview(value: Any, max_len: int) -> Optional[str]:
    """Apply the configured preview policy: capture toggle + clip_preview."""
    tracer = get_tracer()
    if not tracer.config.capture_previews:
        return None
    cap = min(max_len, tracer.config.preview_max_chars)
    return clip_preview(value, cap)


def _json_default(obj: Any) -> Any:
    """Fallback for :func:`json.dumps` on objects we hand through the api hook.

    Hermes-agent emits ``tool_calls`` as ``SimpleNamespace`` (nested, with a
    ``.function`` sub-namespace). json.dumps calls this recursively for any
    non-serialisable object, so returning ``__dict__`` flattens each layer.
    """
    if hasattr(obj, "__dict__") and obj.__dict__:
        return obj.__dict__
    return str(obj)


def _serialize_full(value: Any) -> Optional[str]:
    """JSON-serialise ``value`` in full (no truncation).

    Used for ``capture_full_prompts`` / ``capture_full_responses``: the whole
    point is fidelity, so we skip ``preview_max_chars``. Returns None on
    empty/unserialisable input so the caller can skip setting the attribute.
    """
    if value is None or value == "" or value == [] or value == {}:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=_json_default)
    except Exception:
        try:
            return str(value)
        except Exception:
            return None


def _serialize_conversation_history(history: Any, max_chars: int) -> Optional[str]:
    """Render ``conversation_history`` as a JSON string, clipped to ``max_chars``.

    Returns None when the history is empty or cannot be serialised so the
    caller can fall back to the simple ``user_message`` input.
    """
    if not history:
        return None
    try:
        text = json.dumps(history, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        try:
            text = str(history)
        except Exception:
            return None
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return "." * max_chars
    return text[: max_chars - 3] + "..."


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
    kind = _detect_session_kind(platform, extra_kwargs)
    span_name = "agent" if kind != "cron" else "cron"
    key = f"session:{session_id}"

    attributes = {
        "session.id": truncate_string(session_id, 200),
        "session_id": truncate_string(session_id, 200),
        "hermes.session_id": truncate_string(session_id, 120),
        "hermes.session.kind": kind,
        "llm.model_name": truncate_string(model, 200),
        "llm.provider": truncate_string(platform, 120),
    }
    if synthesized:
        attributes["hermes.session.synthesized"] = True

    cron_job_id = extra_kwargs.get("job_id") or extra_kwargs.get("cron_job_id")
    if cron_job_id:
        attributes["hermes.cron.job_id"] = truncate_string(cron_job_id, 200)

    span = tracer.start_span(
        name=span_name,
        key=key,
        kind="agent",
        attributes=attributes,
        session_id=session_id,
    )
    tracer.spans.push_parent(span, session_id=session_id)
    tracer.register_turn(session_id)
    debug_log(f"  session span started: key={key}, name={span_name}, synthesized={synthesized}")


def on_session_start(session_id: str, model: str, platform: str, **kwargs):
    """Start a top-level session span (or cron span) for the entire run."""
    tracer = get_tracer()
    debug_log(f"on_session_start fired: session={session_id}, platform={platform}")
    if not tracer.is_enabled:
        return

    tracer.sweep_expired_turns()
    tracer.record_metric("session_count", 1, {"session_id": session_id})
    _start_session_span(
        session_id,
        model,
        platform,
        kwargs,
        synthesized=False,
    )


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
    attributes: Dict[str, Any] = {
        "hermes.session.completed": bool(completed),
        "hermes.session.interrupted": bool(interrupted),
        "llm.model_name": truncate_string(model, 200),
        "llm.provider": truncate_string(platform, 120),
    }

    # Drain the aggregators in one shot. Everything this session buffered
    # — I/O, usage totals, turn summary — comes back in a single PerSession.
    ps = tracer.sessions.pop(session_id)

    if ps is not None and ps.io_captured:
        if ps.io.get("input"):
            attributes["input.value"] = ps.io["input"]
        if ps.io.get("output"):
            attributes["output.value"] = ps.io["output"]

    if ps is not None and ps.usage_updated:
        attributes.update(_usage_attributes(ps.usage))

    if ps is not None and ps.sender_id:
        attributes["hermes.sender.id"] = ps.sender_id

    # Per-turn summary roll-up
    if ps is not None:
        summary = ps.turn_summary
        if summary.final_status is None:
            if completed:
                summary.final_status = "completed"
            elif interrupted:
                summary.final_status = "interrupted"
            else:
                summary.final_status = "incomplete"
        attributes.update(_summary_attributes(summary))
    else:
        if completed:
            attributes["hermes.turn.final_status"] = "completed"
        elif interrupted:
            attributes["hermes.turn.final_status"] = "interrupted"
        else:
            attributes["hermes.turn.final_status"] = "incomplete"

    status = "ok" if completed or interrupted else "error"

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


def on_pre_tool_call(tool_name: str, args: dict, task_id: str, **kwargs):
    """Start a tool span before the tool executes."""
    debug_log(f"pre_tool_call fired: tool={tool_name}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return

    tracer.sweep_expired_turns()

    key = f"{tool_name}:{task_id}"
    tracer.sessions.record_tool_start(key, time.perf_counter())

    # OpenInference attributes — Phoenix Info panel
    attributes: Dict[str, Any] = {
        "tool.name": tool_name,
    }
    preview = _preview(json.dumps(args) if args else "{}", 500)
    if preview is not None:
        attributes["input.value"] = preview

    # Richer identity — hermes.tool.* (opt-in namespace)
    target, command = resolve_tool_identity(args)
    if target:
        attributes["hermes.tool.target"] = truncate_string(target, 500)
    if command:
        attributes["hermes.tool.command"] = truncate_string(command, 500)
    skill = infer_skill_name(args)
    if skill:
        attributes["hermes.skill.name"] = skill
        tracer.record_metric(
            "skill_inferred",
            1,
            {"skill_name": skill, "source": "path_match"},
        )

    # Summary roll-up (requires session_id to bucket into the right turn).
    session_id = kwargs.get("session_id")
    if session_id:
        summary = tracer.sessions.get_or_create(session_id).turn_summary
        summary.add_tool(tool_name)
        summary.add_target(target)
        summary.add_command(command)
        summary.add_skill(skill)

    tracer.start_span(
        name=f"tool.{tool_name}",
        key=key,
        kind="tool",
        attributes=attributes,
        session_id=session_id,
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

    start_time = tracer.sessions.pop_tool_start(key)
    if start_time:
        duration_ms = (time.perf_counter() - start_time) * 1000
        tracer.record_metric("tool_duration", duration_ms, {"tool_name": tool_name})

    # Build final attributes — OpenInference conventions for Phoenix Info
    attributes: Dict[str, Any] = {}

    # Parse the result once
    if isinstance(result, dict):
        result_json = result
    else:
        try:
            result_json = json.loads(result) if isinstance(result, str) else {}
        except (json.JSONDecodeError, TypeError):
            result_json = {}

    # Determine outcome taxonomy
    outcome = extract_tool_result_status(result_json) or "completed"
    attributes["hermes.tool.outcome"] = outcome

    # Preserve existing error.message attribute when outcome == error
    has_error = outcome == "error"
    error_msg = ""
    if has_error and isinstance(result_json, dict):
        err_val = result_json.get("error")
        if err_val:
            error_msg = truncate_string(err_val, 500)
            attributes["error.message"] = error_msg

    # OpenInference output value — Phoenix shows this in Info
    preview = _preview(result, 2000)
    if preview is not None:
        attributes["output.value"] = preview

    # Summary roll-up
    session_id = kwargs.get("session_id")
    if session_id:
        summary = tracer.sessions.get_or_create(session_id).turn_summary
        summary.add_outcome(outcome)

    # Map outcome to span status. Only "error" is ERROR; other non-ok outcomes
    # (timeout, blocked, ...) are OK to avoid polluting error rates.
    status = "error" if has_error else "ok"
    tracer.end_span(
        key, attributes=attributes, status=status, error_message=error_msg if has_error else None
    )
    debug_log(f"  span ended: status={status}, outcome={outcome}")


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
    session_key = f"session:{session_id}"
    if session_id and session_key not in tracer.spans._active_spans:
        _start_session_span(
            session_id,
            model,
            platform,
            kwargs,
            synthesized=True,
        )

    key = f"llm:{session_id}"

    # Capture first LLM input for top-level session span
    if session_id:
        ps = tracer.sessions.get_or_create(session_id)
        if not ps.io_captured:
            ps.io["input"] = _preview(user_message, 500) or ""
            ps.io_captured = True

    # OpenInference attributes — Phoenix Info panel
    attributes: Dict[str, Any] = {
        "session.id": truncate_string(session_id, 200),
        "session_id": truncate_string(session_id, 200),
        "llm.model_name": model,
        "llm.provider": platform,
    }

    if tracer.config.capture_sender_id:
        sender_id = truncate_string(kwargs.get("sender_id"), 200)
        if sender_id:
            attributes["hermes.sender.id"] = sender_id
            if session_id:
                tracer.sessions.get_or_create(session_id).sender_id = sender_id

    # Opt-in: put the entire conversation the model is about to see on
    # input.value. Falls back to just the latest user_message otherwise —
    # that's the historical default and what small backends handle best.
    if tracer.config.capture_conversation_history and tracer.config.capture_previews:
        full = _serialize_conversation_history(
            conversation_history,
            tracer.config.conversation_history_max_chars,
        )
        if full is not None:
            attributes["input.value"] = full
            attributes["input.mime_type"] = "application/json"
            attributes["hermes.conversation.message_count"] = len(conversation_history)
        else:
            preview = _preview(user_message, 500)
            if preview is not None:
                attributes["input.value"] = preview
    else:
        preview = _preview(user_message, 500)
        if preview is not None:
            attributes["input.value"] = preview

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

    # Capture last LLM output for top-level session span. Only if the
    # session already has I/O buffered (i.e. pre_llm_call ran) — mirrors
    # prior behaviour where we never wrote output without a matching input.
    if session_id:
        ps = tracer.sessions.peek(session_id)
        if ps is not None and ps.io_captured:
            ps.io["output"] = _preview(assistant_response, 500) or ""

    tracer.record_metric(
        "message_count", 1, {"session_id": session_id, "model": model, "provider": platform}
    )

    # OpenInference attributes — Phoenix Info panel
    attributes: Dict[str, Any] = {
        "session.id": truncate_string(session_id, 200),
        "session_id": truncate_string(session_id, 200),
    }
    preview = _preview(assistant_response, 500)
    if preview is not None:
        attributes["output.value"] = preview

    # Pop parent — tool spans after this won't nest under this LLM call
    tracer.spans.pop_parent(session_id=session_id)

    # Mark as OK — LLM call completed successfully
    tracer.end_span(key, attributes=attributes, status="ok")
    debug_log("  LLM span ended: status=ok")


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
    attributes = {
        "session.id": truncate_string(session_id, 200),
        "session_id": truncate_string(session_id, 200),
        "llm.model_name": model,
        "llm.provider": provider,
        "llm.api_mode": api_mode,
        "llm.request.message_count": message_count,
        "llm.request.approx_input_tokens": approx_input_tokens,
    }
    if max_tokens:
        attributes["llm.request.max_tokens"] = max_tokens

    if tracer.config.capture_full_prompts:
        messages = kwargs.get("messages")
        system_prompt = kwargs.get("system_prompt")
        serialized = _serialize_full(messages)
        if serialized is not None:
            attributes["llm.input_messages"] = serialized
            attributes["input.value"] = serialized
            attributes["input.mime_type"] = "application/json"
        if system_prompt:
            attributes["llm.system_prompt"] = str(system_prompt)

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

    # Token usage — dual convention (gen_ai.usage.* + llm.token_count.*).
    # See _usage_attributes for the full attribute list.
    if usage:
        totals = _normalize_usage(usage)
        attributes.update(_usage_attributes(totals))

        # Roll up usage to the top-level session/cron span.
        if session_id:
            ps = tracer.sessions.get_or_create(session_id)
            for field in _USAGE_FIELDS:
                ps.usage[field] += totals[field]
            ps.usage_updated = True

        # Record metrics
        metric_attrs: Dict[str, Any] = {"model": model, "provider": provider}
        if session_id:
            metric_attrs["session_id"] = session_id
        _record_usage_metrics(tracer, totals, metric_attrs)

        cost = usage.get("cost")
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

    if tracer.config.capture_full_responses:
        response_content = kwargs.get("response_content")
        response_tool_calls = kwargs.get("response_tool_calls")
        if response_content:
            attributes["llm.output.content"] = str(response_content)
            attributes["output.value"] = str(response_content)
            attributes["output.mime_type"] = "text/plain"
        tool_calls_serialized = _serialize_full(response_tool_calls)
        if tool_calls_serialized is not None:
            attributes["llm.output.tool_calls"] = tool_calls_serialized
            if not response_content:
                attributes["output.value"] = tool_calls_serialized
                attributes["output.mime_type"] = "application/json"

    # Pop parent
    tracer.spans.pop_parent(session_id=session_id)

    # Mark as OK
    tracer.end_span(key, attributes=attributes, status="ok")
    debug_log(f"  API span ended: status=ok, tokens={usage.get('total_tokens', 0) if usage else 0}")
