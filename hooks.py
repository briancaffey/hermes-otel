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
    coerce_bool,
    extract_tool_result_status,
    http_status_class,
    infer_skill_name,
    resolve_tool_identity,
    subagent_span_key,
    subagent_status_to_span_status,
    to_optional_int,
    truncate_string,
)
from .session_state import TurnSummary
from .tracer import get_tracer

try:
    from opentelemetry.trace import Link
except ImportError:  # pragma: no cover — plugin is unusable without OTel
    Link = None  # type: ignore[assignment]


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
    "reasoning_tokens",
)


def _normalize_usage(usage: dict) -> Dict[str, int]:
    """Parse a raw hermes ``usage`` dict into canonical token totals.

    Hermes exposes ``output_tokens``; some providers use ``completion_tokens``.
    Similarly ``input_tokens`` vs ``prompt_tokens``. Total is derived from
    the reported value or sum(prompt, completion) when absent. ``reasoning_tokens``
    is a *subset* of ``completion_tokens`` (the thinking portion of the output),
    not an additive bucket, so it is never folded into ``total_tokens``. Returns
    all canonical fields, zero-filled.
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
        "reasoning_tokens": _to_int(usage.get("reasoning_tokens")),
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
    reasoning = totals.get("reasoning_tokens", 0)

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
        # Current OTel GenAI spelling, plus the pre-existing alias for
        # backwards compatibility with dashboards created before this change.
        attrs["gen_ai.usage.cache_read.input_tokens"] = cache_read
        attrs["gen_ai.usage.cache_read_input_tokens"] = cache_read
    if cache_write:
        attrs["llm.token_count.prompt_details.cache_write"] = cache_write
        attrs["gen_ai.usage.cache_creation.input_tokens"] = cache_write
        attrs["gen_ai.usage.cache_creation_input_tokens"] = cache_write
    if reasoning:
        # Reasoning ("thinking") tokens are a subset of the output/completion
        # count, surfaced as a breakdown. OpenInference (Phoenix) reads
        # ``completion_details.reasoning``; OTel GenAI uses
        # ``gen_ai.usage.reasoning.output_tokens``.
        attrs["llm.token_count.completion_details.reasoning"] = reasoning
        attrs["gen_ai.usage.reasoning.output_tokens"] = reasoning
    return attrs


_USAGE_METRIC_LABELS = (
    ("prompt_tokens", "input"),
    ("completion_tokens", "output"),
    ("cache_read_tokens", "cacheRead"),
    ("cache_write_tokens", "cacheCreation"),
    ("reasoning_tokens", "reasoning"),
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


def _preview(value: Any, max_chars: int) -> Optional[str]:
    """Apply the configured preview policy: capture toggle + clip_preview."""
    tracer = get_tracer()
    if not tracer.config.capture_previews:
        return None
    return clip_preview(value, max_chars)


def _sender_attributes(sender_id: str, platform: str) -> Dict[str, str]:
    """Return backend-neutral sender attributes for trace/user filtering."""
    if not sender_id:
        return {}
    attrs = {"hermes.sender.id": sender_id}
    attrs["user.id"] = f"{platform}:{sender_id}" if platform else sender_id
    return attrs


def _session_sender_attributes(tracer, session_id: Optional[str]) -> Dict[str, str]:
    """Return sender attributes already captured for a session."""
    if not session_id:
        return {}
    ps = tracer.sessions.peek(session_id)
    return _per_session_sender_attributes(ps)


def _gen_ai_attributes(
    session_id: Optional[str],
    operation_name: str,
    extra_kwargs: Optional[dict] = None,
) -> Dict[str, str]:
    """Return common OpenTelemetry GenAI attributes."""
    attrs: Dict[str, str] = {
        "gen_ai.operation.name": operation_name,
    }
    session_text = truncate_string(session_id, 200)
    if session_text:
        attrs["gen_ai.conversation.id"] = session_text
    return attrs


def _provider_attributes(provider: Any) -> Dict[str, str]:
    """Return current and compatibility provider attributes."""
    value = truncate_string(provider, 120)
    if not value:
        return {}
    return {
        "gen_ai.provider.name": value,
        # Kept for older OTel drafts and existing dashboards.
        "gen_ai.system": value,
    }


def _optional_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gen_ai_request_param_attributes(kwargs: dict) -> Dict[str, Any]:
    """Best-effort standard GenAI request parameter attributes."""
    attrs: Dict[str, Any] = {}
    for src, dest in (
        ("temperature", "gen_ai.request.temperature"),
        ("top_p", "gen_ai.request.top_p"),
        ("frequency_penalty", "gen_ai.request.frequency_penalty"),
        ("presence_penalty", "gen_ai.request.presence_penalty"),
    ):
        value = _optional_number(kwargs.get(src))
        if value is not None:
            attrs[dest] = value

    top_k = kwargs.get("top_k")
    if top_k is not None and not isinstance(top_k, bool):
        try:
            attrs["gen_ai.request.top_k"] = int(top_k)
        except (TypeError, ValueError):
            pass

    for key in ("stream", "streaming", "is_streaming"):
        if key in kwargs:
            attrs["gen_ai.request.stream"] = bool(kwargs[key])
            break

    reasoning = (
        kwargs.get("reasoning_level") or kwargs.get("reasoning_effort") or kwargs.get("reasoning")
    )
    reasoning = truncate_string(reasoning, 120) if reasoning is not None else ""
    if reasoning:
        attrs["gen_ai.request.reasoning.level"] = reasoning

    stop_sequences = kwargs.get("stop_sequences") or kwargs.get("stop")
    if isinstance(stop_sequences, str):
        attrs["gen_ai.request.stop_sequences"] = [stop_sequences]
    elif isinstance(stop_sequences, (list, tuple)) and stop_sequences:
        attrs["gen_ai.request.stop_sequences"] = [truncate_string(v, 200) for v in stop_sequences]

    choice_count = kwargs.get("choice_count") or kwargs.get("n")
    if choice_count is not None and not isinstance(choice_count, bool):
        try:
            n = int(choice_count)
            if n != 1:
                attrs["gen_ai.request.choice.count"] = n
        except (TypeError, ValueError):
            pass
    return attrs


def _extract_correlation_id(extra_kwargs: dict) -> str:
    """Return an incoming correlation identifier from hook kwargs, if present.

    Different callers spell this value differently. Accept the common Python
    snake_case form, the canonical OTel attribute key, and the HTTP/W3C-ish
    hyphenated form so gateways, cron, webhooks, and API callers can pass it
    through without adapter-specific glue.
    """

    for key in (
        "correlation_id",
        "correlation.id",
        "correlation-id",
        "x_correlation_id",
        "x-correlation-id",
    ):
        raw = extra_kwargs.get(key)
        if raw is None:
            continue
        value = truncate_string(raw, 200)
        if value:
            return value
    return ""


def _correlation_attributes(
    tracer, session_id: Optional[str], extra_kwargs: dict
) -> Dict[str, str]:
    """Build stable correlation attributes for a hook callback.

    Preference order:
    1. Incoming correlation ID supplied by the host app/hook kwargs.
    2. Previously resolved per-session correlation ID.
    3. The Hermes session ID as deterministic fallback.

    Using the session ID fallback keeps today's Hermes traces queryable by a
    stable ``correlation.id`` without requiring gateway/core changes first.
    When a true upstream boundary provides a correlation ID, it wins and is
    reused for all later spans in the same session.
    """

    incoming = _extract_correlation_id(extra_kwargs)
    session_text = truncate_string(session_id, 200)
    session_key = str(session_id) if session_id else ""
    correlation_id = incoming

    if session_key:
        ps = tracer.sessions.get_or_create(session_key)
        if incoming:
            ps.correlation_id = incoming
        elif ps.correlation_id:
            correlation_id = ps.correlation_id
        else:
            correlation_id = session_text
            ps.correlation_id = correlation_id

    if not correlation_id:
        return {}
    return {"correlation.id": truncate_string(correlation_id, 200)}


def _per_session_sender_attributes(ps: Any) -> Dict[str, str]:
    """Return sender attributes from a PerSession aggregator."""
    if ps is None or not ps.sender_id:
        return {}
    attrs = {"hermes.sender.id": ps.sender_id}
    if ps.user_id:
        attrs["user.id"] = ps.user_id
    return attrs


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
        "gen_ai.request.model": truncate_string(model, 200),
    }
    attributes.update(_provider_attributes(extra_kwargs.get("provider") or platform))
    attributes.update(_gen_ai_attributes(session_id, "invoke_agent", extra_kwargs))
    attributes.update(_correlation_attributes(tracer, session_id, extra_kwargs))
    if synthesized:
        attributes["hermes.session.synthesized"] = True

    cron_job_id = extra_kwargs.get("job_id") or extra_kwargs.get("cron_job_id")
    if cron_job_id:
        attributes["hermes.cron.job_id"] = truncate_string(cron_job_id, 200)

    # Sub-agent rejoin: if this session is a delegated child (its session_id was
    # registered by on_subagent_start in the parent), nest its root span under
    # the delegation span so the whole multi-agent run is one connected trace.
    # In-process delegation has the live span → real parent. Cross-process only
    # has the SpanContext → attach a link instead (best-effort correlation).
    parent_override = None
    links = None
    record = tracer._subagent_registry.get(session_id)
    if record:
        attributes["hermes.session.is_subagent"] = True
        if record.get("role"):
            attributes["hermes.subagent.role"] = truncate_string(record["role"], 200)
        if record.get("parent_session_id"):
            attributes["hermes.subagent.parent_session_id"] = truncate_string(
                record["parent_session_id"], 200
            )
        span_obj = record.get("span")
        if span_obj is not None and hasattr(span_obj, "get_span_context"):
            parent_override = span_obj
        elif record.get("context") is not None and Link is not None:
            links = [Link(record["context"])]

    span = tracer.start_span(
        name=span_name,
        key=key,
        kind="agent",
        attributes=attributes,
        session_id=session_id,
        parent=parent_override,
        links=links,
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
        "gen_ai.response.model": truncate_string(model, 200),
    }
    attributes.update(_provider_attributes(kwargs.get("provider") or platform))
    attributes.update(_gen_ai_attributes(session_id, "invoke_agent", kwargs))
    attributes.update(_correlation_attributes(tracer, session_id, kwargs))

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

    # Surface the last API error's type on the root so a failed turn shows why.
    if ps is not None and ps.last_error_type:
        attributes["error.type"] = ps.last_error_type

    attributes.update(_per_session_sender_attributes(ps))

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
        "gen_ai.tool.name": tool_name,
    }
    preview = _preview(
        json.dumps(args) if args else "{}",
        tracer.config.tool_input_preview_max_chars or tracer.config.preview_max_chars,
    )
    if preview is not None:
        attributes["input.value"] = preview
    if (
        tracer.config.capture_previews
        and tool_name.startswith("mcp_")
        and tracer.config.capture_full_prompts
    ):
        serialized_args = _serialize_full(args)
        if serialized_args is not None:
            attributes["gen_ai.tool.call.arguments"] = serialized_args

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
        attributes.update(_gen_ai_attributes(session_id, "execute_tool", kwargs))
        attributes.update(_correlation_attributes(tracer, session_id, kwargs))
        attributes.update(_session_sender_attributes(tracer, session_id))
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
        tracer.record_metric(
            "tool_duration",
            duration_ms,
            {"tool_name": tool_name, "gen_ai.tool.name": tool_name},
        )

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
    preview = _preview(
        result,
        tracer.config.tool_output_preview_max_chars or tracer.config.preview_max_chars,
    )
    if preview is not None:
        attributes["output.value"] = preview
    if (
        tracer.config.capture_previews
        and tool_name.startswith("mcp_")
        and tracer.config.capture_full_responses
    ):
        serialized_result = _serialize_full(result_json if result_json else result)
        if serialized_result is not None:
            attributes["gen_ai.tool.call.result"] = serialized_result

    # Summary roll-up
    session_id = kwargs.get("session_id")
    if session_id:
        attributes.update(_gen_ai_attributes(session_id, "execute_tool", kwargs))
        attributes.update(_correlation_attributes(tracer, session_id, kwargs))
        attributes.update(_session_sender_attributes(tracer, session_id))
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
            ps.io["input"] = (
                _preview(
                    user_message,
                    tracer.config.llm_input_preview_max_chars or tracer.config.preview_max_chars,
                )
                or ""
            )
            ps.io_captured = True

    # OpenInference attributes — Phoenix Info panel
    attributes: Dict[str, Any] = {
        "session.id": truncate_string(session_id, 200),
        "session_id": truncate_string(session_id, 200),
        "llm.model_name": model,
        "llm.provider": platform,
        "gen_ai.request.model": truncate_string(model, 200),
    }
    attributes.update(_provider_attributes(platform))
    attributes.update(_gen_ai_attributes(session_id, "chat", kwargs))
    attributes.update(_correlation_attributes(tracer, session_id, kwargs))

    if tracer.config.capture_sender_id:
        sender_id = truncate_string(kwargs.get("sender_id"), 200)
        if sender_id:
            sender_platform = truncate_string(platform, 120)
            sender_attrs = _sender_attributes(sender_id, sender_platform)
            attributes.update(sender_attrs)
            if session_id:
                ps = tracer.sessions.get_or_create(session_id)
                ps.sender_id = sender_id
                ps.user_id = sender_attrs["user.id"]

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
            preview = _preview(
                user_message,
                tracer.config.llm_input_preview_max_chars or tracer.config.preview_max_chars,
            )
            if preview is not None:
                attributes["input.value"] = preview
    else:
        preview = _preview(
            user_message,
            tracer.config.llm_input_preview_max_chars or tracer.config.preview_max_chars,
        )
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
            ps.io["output"] = (
                _preview(
                    assistant_response,
                    tracer.config.llm_output_preview_max_chars or tracer.config.preview_max_chars,
                )
                or ""
            )

    tracer.record_metric(
        "message_count", 1, {"session_id": session_id, "model": model, "provider": platform}
    )

    # OpenInference attributes — Phoenix Info panel
    attributes: Dict[str, Any] = {
        "session.id": truncate_string(session_id, 200),
        "session_id": truncate_string(session_id, 200),
        "gen_ai.response.model": truncate_string(model, 200),
    }
    attributes.update(_provider_attributes(platform))
    attributes.update(_gen_ai_attributes(session_id, "chat", kwargs))
    attributes.update(_correlation_attributes(tracer, session_id, kwargs))
    preview = _preview(
        assistant_response,
        tracer.config.llm_output_preview_max_chars or tracer.config.preview_max_chars,
    )
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
        "gen_ai.request.model": truncate_string(model, 200),
    }
    attributes.update(_provider_attributes(provider))
    attributes.update(_gen_ai_attributes(session_id, "chat", kwargs))
    attributes.update(_gen_ai_request_param_attributes(kwargs))
    attributes.update(_correlation_attributes(tracer, session_id, kwargs))
    if max_tokens:
        attributes["llm.request.max_tokens"] = max_tokens
        attributes["gen_ai.request.max_tokens"] = max_tokens

    attributes.update(_session_sender_attributes(tracer, session_id))

    if tracer.config.capture_full_prompts:
        messages = kwargs.get("messages")
        system_prompt = kwargs.get("system_prompt")
        serialized = _serialize_full(messages)
        if serialized is not None:
            attributes["llm.input_messages"] = serialized
            attributes["gen_ai.input.messages"] = serialized
            attributes["input.value"] = serialized
            attributes["input.mime_type"] = "application/json"
        if system_prompt:
            attributes["llm.system_prompt"] = str(system_prompt)
            attributes["gen_ai.system_instructions"] = str(system_prompt)

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
    attributes.update(_gen_ai_attributes(session_id, "chat", kwargs))
    attributes.update(_provider_attributes(provider))
    response_model_value = response_model or model
    if response_model_value:
        attributes["gen_ai.response.model"] = truncate_string(response_model_value, 200)
    response_id = kwargs.get("response_id") or kwargs.get("id")
    if response_id:
        attributes["gen_ai.response.id"] = truncate_string(response_id, 200)
    attributes.update(_correlation_attributes(tracer, session_id, kwargs))

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
        attributes["gen_ai.response.finish_reasons"] = [finish_reason]
    if assistant_content_chars:
        attributes["llm.response.output_chars"] = assistant_content_chars
    if assistant_tool_call_count:
        attributes["llm.response.tool_calls"] = assistant_tool_call_count

    if tracer.config.capture_full_responses:
        response_content = kwargs.get("response_content")
        response_tool_calls = kwargs.get("response_tool_calls")
        if response_content:
            response_text = str(response_content)
            attributes["llm.output.content"] = response_text
            attributes["gen_ai.output.messages"] = json.dumps(
                [{"role": "assistant", "content": response_text}],
                ensure_ascii=False,
            )
            attributes["output.value"] = response_text
            attributes["output.mime_type"] = "text/plain"
        tool_calls_serialized = _serialize_full(response_tool_calls)
        if tool_calls_serialized is not None:
            attributes["llm.output.tool_calls"] = tool_calls_serialized
            if not response_content:
                attributes["gen_ai.output.messages"] = tool_calls_serialized
                attributes["output.value"] = tool_calls_serialized
                attributes["output.mime_type"] = "application/json"

    # Pop parent
    tracer.spans.pop_parent(session_id=session_id)

    # Mark as OK
    tracer.end_span(key, attributes=attributes, status="ok")
    debug_log(f"  API span ended: status=ok, tokens={usage.get('total_tokens', 0) if usage else 0}")


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

    error = error or {}
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
    attributes.update(_gen_ai_attributes(session_id, "chat", kwargs))
    attributes.update(_provider_attributes(provider or platform))
    attributes.update(_correlation_attributes(tracer, session_id, kwargs))

    # Remember why this turn failed so on_session_end can surface it on the root.
    if session_id and error_type:
        tracer.sessions.get_or_create(session_id).last_error_type = error_type

    key = f"api:{task_id}" if task_id else None
    span = tracer.spans.get_span(key) if key else None
    created_fallback = False

    if span is None:
        # No in-flight api span (error before pre_api_request, or already swept).
        # Create a short-lived span so the failure is still visible. Fail-open.
        fallback_key = key or f"api.error:{kwargs.get('api_request_id') or session_id or 'unknown'}"
        identity: Dict[str, Any] = {
            "session.id": truncate_string(session_id, 200),
            "session_id": truncate_string(session_id, 200),
        }
        if model:
            identity["llm.model_name"] = model
            identity["gen_ai.request.model"] = truncate_string(model, 200)
        if provider:
            identity["llm.provider"] = provider
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

    # Metrics. Keep labels low-cardinality.
    metric_attrs: Dict[str, Any] = {
        "error_type": error_type or "unknown",
        "status_class": status_class,
        "retryable": str(bool(is_retryable)).lower(),
    }
    if model:
        metric_attrs["model"] = model
    if provider:
        metric_attrs["provider"] = provider
    tracer.record_metric("api_error_count", 1, metric_attrs)
    # Count a retry attempt only when the failure was actually retryable.
    if is_retryable:
        retry_attrs: Dict[str, Any] = {}
        if model:
            retry_attrs["model"] = model
        if provider:
            retry_attrs["provider"] = provider
        tracer.record_metric("retry_count", 1, retry_attrs)

    debug_log(f"  API error span ended: key={key}, error.type={error_type}, class={status_class}")


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
    if parent_session_id:
        attributes["session.id"] = truncate_string(parent_session_id, 200)
        attributes["session_id"] = truncate_string(parent_session_id, 200)
        attributes["hermes.subagent.parent_session_id"] = truncate_string(parent_session_id, 200)
    parent_turn_id = kwargs.get("parent_turn_id")
    if parent_turn_id:
        attributes["hermes.subagent.parent_turn_id"] = truncate_string(parent_turn_id, 200)
    child_subagent_id = kwargs.get("child_subagent_id")
    if child_subagent_id:
        attributes["hermes.subagent.child_id"] = truncate_string(child_subagent_id, 200)
    parent_subagent_id = kwargs.get("parent_subagent_id")
    if parent_subagent_id:
        attributes["hermes.subagent.parent_id"] = truncate_string(parent_subagent_id, 200)
    goal_preview = _preview(
        child_goal,
        tracer.config.tool_input_preview_max_chars or tracer.config.preview_max_chars,
    )
    if goal_preview is not None:
        attributes["hermes.subagent.goal"] = goal_preview
        attributes["input.value"] = goal_preview
    attributes.update(_correlation_attributes(tracer, parent_session_id, kwargs))

    # Nest under the parent session's in-flight span (api/llm/session). The
    # delegation span is NOT pushed as a parent — the parent session keeps
    # working on its own stack; this span is a side branch that the child
    # rejoins via _subagent_registry.
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
    tracer._subagent_registry[str(child_session_id)] = record
    debug_log(f"  subagent span started: key={key}, name={span_name}")


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

    record = tracer._subagent_registry.pop(str(child_session_id), None)
    role = (record.get("role") if record else None) or (
        truncate_string(child_role, 200) if child_role else "subagent"
    )

    status = subagent_status_to_span_status(child_status)
    attributes: Dict[str, Any] = {}
    if child_status:
        attributes["hermes.subagent.status"] = truncate_string(child_status, 120)
    if duration_ms is not None:
        try:
            attributes["hermes.subagent.duration_ms"] = round(float(duration_ms), 1)
        except (TypeError, ValueError):
            pass
    summary_preview = _preview(
        child_summary,
        tracer.config.tool_output_preview_max_chars or tracer.config.preview_max_chars,
    )
    if summary_preview is not None:
        attributes["hermes.subagent.summary"] = summary_preview
        attributes["output.value"] = summary_preview

    error_message = None
    if status == "error":
        error_message = truncate_string(child_summary or child_status, 500)

    tracer.end_span(key, attributes=attributes, status=status, error_message=error_message)

    # Metrics
    tracer.record_metric("subagent_count", 1, {"role": role, "status": status})
    if duration_ms is not None:
        try:
            tracer.record_metric("subagent_duration", float(duration_ms), {"role": role})
        except (TypeError, ValueError):
            pass
    debug_log(f"  subagent span ended: key={key}, status={status}")


# ── Distributed trace-context propagation (W3C traceparent) ──────────────────
#
# Downstream services (notably MCP servers reached over HTTP) can be linked
# into the agent's trace by forwarding the active span's context as a W3C
# ``traceparent`` header. The span registry (``tracer.spans``) is a
# process-global dict keyed by ``session_id`` holding real OTel spans, so these
# helpers are safe to call from any thread or event loop — including the MCP
# background loop, which does not inherit the agent task's OTel context var.


def get_current_traceparent(session_id: Optional[str] = None) -> Optional[str]:
    """Return the W3C ``traceparent`` for the active span, or ``None``.

    Format: ``00-<32-hex trace id>-<16-hex span id>-<2-hex flags>`` (the
    ``traceparent`` header defined by https://www.w3.org/TR/trace-context/).

    Pass ``session_id`` when you know it — the lookup is session-keyed and
    survives thread/event-loop boundaries. With no ``session_id`` (or an
    unknown one) it falls back to the current context-var parent and, as a
    last resort, the innermost span of any single active session. Returns
    ``None`` when tracing is disabled or no valid span is active.
    """
    tracer = get_tracer()
    spans = getattr(tracer, "spans", None)
    if spans is None:
        return None

    parent = spans.get_current_parent(session_id)
    if parent is None:
        # Last resort: any active session's innermost span. Helps callers on a
        # background loop (e.g. an outbound MCP request) that can't supply a
        # session_id when exactly one session is in flight.
        stacks = getattr(spans, "_session_parent_stacks", {})
        for stack in reversed(list(stacks.values())):
            if stack:
                parent = stack[-1]
                break
    if parent is None:
        return None

    get_ctx = getattr(parent, "get_span_context", None)
    if get_ctx is None:
        return None
    ctx = get_ctx()
    if ctx is None or not getattr(ctx, "is_valid", False):
        return None

    flags = "01" if (ctx.trace_flags & 1) else "00"
    return f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{flags}"


def on_mcp_request_headers(
    server_name: Optional[str] = None,
    tool_name: Optional[str] = None,
    session_id: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, str]:
    """``mcp_request_headers`` hook — inject ``traceparent`` on outbound MCP calls.

    Returns a header dict merged onto the outbound MCP HTTP request by
    hermes-agent. Empty dict when no trace is active (never raises, never
    blocks the call). ``session_id`` may also arrive nested in ``kwargs``.
    """
    sid = session_id or kwargs.get("session_id")
    traceparent = get_current_traceparent(sid)
    return {"traceparent": traceparent} if traceparent else {}
