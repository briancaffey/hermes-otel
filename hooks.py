"""Hermes OTel plugin — hook callbacks.

Each hook starts or ends a span, passing data through to OTel attributes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .debug_utils import debug_log
from .helpers import (
    clip_preview,
    extract_tool_result_status,
    infer_skill_name,
    resolve_tool_identity,
)
from .tracer import get_tracer

# Per-session token aggregation for top-level session/cron spans.
_SESSION_USAGE: dict[str, dict[str, int]] = {}

# Per-session I/O for top-level agent span in Phoenix.
_SESSION_IO: dict[str, dict[str, str]] = {}

# Track tool start times for duration calculation.
_TOOL_START_TIMES: dict[str, float] = {}


@dataclass
class TurnSummary:
    """Per-session aggregator of per-turn telemetry.

    Flushed onto the session/agent span in on_session_end. Also usable as a
    fallback on on_post_llm_call when no session hook is available.
    """

    tool_names: Set[str] = field(default_factory=set)
    tool_targets: List[str] = field(default_factory=list)  # preserves order for "first N chars"
    tool_commands: List[str] = field(default_factory=list)
    tool_outcomes: Set[str] = field(default_factory=set)
    skill_names: Set[str] = field(default_factory=set)
    api_call_count: int = 0
    final_status: Optional[str] = None

    _seen_targets: Set[str] = field(default_factory=set)
    _seen_commands: Set[str] = field(default_factory=set)

    def add_tool(self, name: str) -> None:
        if name:
            self.tool_names.add(name)

    def add_target(self, target: Optional[str]) -> None:
        if target and target not in self._seen_targets:
            self._seen_targets.add(target)
            self.tool_targets.append(target)

    def add_command(self, command: Optional[str]) -> None:
        if command and command not in self._seen_commands:
            self._seen_commands.add(command)
            self.tool_commands.append(command)

    def add_outcome(self, outcome: Optional[str]) -> None:
        if outcome:
            self.tool_outcomes.add(outcome)

    def add_skill(self, skill: Optional[str]) -> None:
        if skill:
            self.skill_names.add(skill)


# Per-session turn summaries.
_SESSION_TURN_SUMMARY: dict[str, TurnSummary] = {}


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


def _get_or_create_summary(session_id: str) -> TurnSummary:
    summary = _SESSION_TURN_SUMMARY.get(session_id)
    if summary is None:
        summary = TurnSummary()
        _SESSION_TURN_SUMMARY[session_id] = summary
    return summary


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


# ── Legacy helpers kept for backward compatibility ─────────────────────────
# (Existing tests import _safe_str from hooks; we delegate to clip_preview for
# preview emission but keep _safe_str for non-preview string fields and for
# callers that depend on its exact semantics.)


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


def _preview(value: Any, max_len: int) -> Optional[str]:
    """Apply the configured preview policy: capture toggle + clip_preview."""
    tracer = get_tracer()
    if not tracer.config.capture_previews:
        return None
    cap = min(max_len, tracer.config.preview_max_chars)
    return clip_preview(value, cap)


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
        "session.id": _safe_str(session_id, 200),
        "session_id": _safe_str(session_id, 200),
        "hermes.session_id": _safe_str(session_id, 120),
        "hermes.session.kind": kind,
        "llm.model_name": _safe_str(model, 200),
        "llm.provider": _safe_str(platform, 120),
    }
    if synthesized:
        attributes["hermes.session.synthesized"] = True

    cron_job_id = extra_kwargs.get("job_id") or extra_kwargs.get("cron_job_id")
    if cron_job_id:
        attributes["hermes.cron.job_id"] = _safe_str(cron_job_id, 200)

    span = tracer.start_span(
        name=span_name,
        key=key,
        kind="agent",
        attributes=attributes,
        session_id=session_id,
    )
    tracer.spans.push_parent(span, session_id=session_id)
    tracer.register_turn(session_id)
    debug_log(
        f"  session span started: key={key}, name={span_name}, synthesized={synthesized}"
    )


def on_session_start(session_id: str, model: str, platform: str, **kwargs):
    """Start a top-level session span (or cron span) for the entire run."""
    tracer = get_tracer()
    debug_log(f"on_session_start fired: session={session_id}, platform={platform}")
    if not tracer.is_enabled:
        return

    tracer.sweep_expired_turns()
    tracer.record_metric("session_count", 1, {"session_id": session_id})
    _start_session_span(
        session_id, model, platform, kwargs, synthesized=False,
    )


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
            attributes["llm.token_count.prompt_details.cache_read"] = usage_totals["cache_read_tokens"]
            attributes["gen_ai.usage.cache_read_input_tokens"] = usage_totals["cache_read_tokens"]
        if usage_totals.get("cache_write_tokens", 0):
            attributes["llm.token_count.prompt_details.cache_write"] = usage_totals["cache_write_tokens"]
            attributes["gen_ai.usage.cache_creation_input_tokens"] = usage_totals["cache_write_tokens"]

    # Per-turn summary roll-up
    summary = _SESSION_TURN_SUMMARY.pop(session_id, None)
    if summary is not None:
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
    _TOOL_START_TIMES[key] = time.perf_counter()

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
        attributes["hermes.tool.target"] = _safe_str(target, 500)
    if command:
        attributes["hermes.tool.command"] = _safe_str(command, 500)
    skill = infer_skill_name(args)
    if skill:
        attributes["hermes.skill.name"] = skill
        tracer.record_metric(
            "skill_inferred", 1,
            {"skill_name": skill, "source": "path_match"},
        )

    # Summary roll-up (requires session_id to bucket into the right turn).
    session_id = kwargs.get("session_id")
    if session_id:
        summary = _get_or_create_summary(session_id)
        summary.add_tool(tool_name)
        summary.add_target(target)
        summary.add_command(command)
        summary.add_skill(skill)

    span = tracer.start_span(
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

    start_time = _TOOL_START_TIMES.pop(key, None)
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
            error_msg = _safe_str(err_val, 500)
            attributes["error.message"] = error_msg

    # OpenInference output value — Phoenix shows this in Info
    preview = _preview(result, 2000)
    if preview is not None:
        attributes["output.value"] = preview

    # Summary roll-up
    session_id = kwargs.get("session_id")
    if session_id:
        summary = _get_or_create_summary(session_id)
        summary.add_outcome(outcome)

    # Map outcome to span status. Only "error" is ERROR; other non-ok outcomes
    # (timeout, blocked, ...) are OK to avoid polluting error rates.
    status = "error" if has_error else "ok"
    tracer.end_span(key, attributes=attributes, status=status, error_message=error_msg if has_error else None)
    debug_log(f"  span ended: status={status}, outcome={outcome}")


def on_pre_llm_call(session_id: str, user_message: str, conversation_history: list,
                    is_first_turn: bool, model: str, platform: str, **kwargs):
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
            session_id, model, platform, kwargs, synthesized=True,
        )

    key = f"llm:{session_id}"

    # Capture first LLM input for top-level session span
    if session_id not in _SESSION_IO:
        preview = _preview(user_message, 500)
        _SESSION_IO[session_id] = {"input": preview or "", "output": ""}

    # OpenInference attributes — Phoenix Info panel
    attributes: Dict[str, Any] = {
        "session.id": _safe_str(session_id, 200),
        "session_id": _safe_str(session_id, 200),
        "llm.model_name": model,
        "llm.provider": platform,
    }

    # Opt-in: put the entire conversation the model is about to see on
    # input.value. Falls back to just the latest user_message otherwise —
    # that's the historical default and what small backends handle best.
    if tracer.config.capture_conversation_history and tracer.config.capture_previews:
        full = _serialize_conversation_history(
            conversation_history, tracer.config.conversation_history_max_chars,
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
        _SESSION_IO[session_id]["output"] = _preview(assistant_response, 500) or ""

    tracer.record_metric("message_count", 1, {"session_id": session_id, "model": model, "provider": platform})

    # OpenInference attributes — Phoenix Info panel
    attributes: Dict[str, Any] = {
        "session.id": _safe_str(session_id, 200),
        "session_id": _safe_str(session_id, 200),
    }
    preview = _preview(assistant_response, 500)
    if preview is not None:
        attributes["output.value"] = preview

    # Pop parent — tool spans after this won't nest under this LLM call
    tracer.spans.pop_parent(session_id=session_id)

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

    tracer.sweep_expired_turns()

    key = f"api:{task_id}"

    # Per-turn summary: count api requests
    if session_id:
        summary = _get_or_create_summary(session_id)
        summary.api_call_count += 1

    # OpenInference attributes — Phoenix Info panel
    attributes = {
        "session.id": _safe_str(session_id, 200),
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
        session_id=session_id,
    )

    # Push as parent — tool spans during this API call will nest under it
    tracer.spans.push_parent(span, session_id=session_id)
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
            attributes["llm.token_count.prompt_details.cache_read"] = cache_read
            attributes["gen_ai.usage.cache_read_input_tokens"] = cache_read
        if cache_write:
            attributes["llm.token_count.prompt_details.cache_write"] = cache_write
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
    tracer.spans.pop_parent(session_id=session_id)

    # Mark as OK
    tracer.end_span(key, attributes=attributes, status="ok")
    debug_log(f"  API span ended: status=ok, tokens={usage.get('total_tokens', 0) if usage else 0}")
