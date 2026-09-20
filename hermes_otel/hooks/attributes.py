"""Attribute builders shared by the hook callbacks.

Every span attribute that more than one hook emits is built here, once, so the
conventions (dual OpenInference + OTel GenAI spelling, truncation limits,
identity keys) cannot drift between hooks (#104).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..helpers import clip_joined, optional_number, package_version, truncate_string
from ..session_state import TurnSummary

_DEFAULT_AGENT_NAME = "hermes-agent"

# Truncation limits for identifiers. One place, so root / llm / api / tool
# spans agree (they used to mix 120 and 200 for the same id).
_ID_MAX = 200
_PROVIDER_MAX = 120


# ── Identity ─────────────────────────────────────────────────────────────────


def _session_identity_attributes(
    session_id: Optional[str], *, root: bool = False
) -> Dict[str, str]:
    """``session.id`` (OTel) and ``session_id`` (legacy) for a span.

    The turn root additionally carries ``hermes.session_id``, the plugin's
    original spelling, so dashboards built on it keep working. Empty when
    there is no session.
    """
    if not session_id:
        return {}
    text = truncate_string(session_id, _ID_MAX)
    attrs = {"session.id": text, "session_id": text}
    if root:
        attrs["hermes.session_id"] = text
    return attrs


def _provider_attributes(provider: Any) -> Dict[str, str]:
    """Return current and compatibility provider attributes."""
    value = truncate_string(provider, _PROVIDER_MAX) if provider else ""
    if not value:
        return {}
    return {
        "gen_ai.provider.name": value,
        # Kept for older OTel drafts and existing dashboards.
        "gen_ai.system": value,
    }


def _model_attributes(model: Any, provider: Any, gen_ai_provider: Any = None) -> Dict[str, str]:
    """Model / provider identity in both conventions.

    ``llm.model_name`` / ``llm.provider`` (OpenInference), ``gen_ai.request.model``
    and the ``gen_ai.provider.name`` / ``gen_ai.system`` pair. ``provider`` is
    what OpenInference sees (the platform on session spans); ``gen_ai_provider``
    overrides the GenAI pair when the real LLM provider is known separately.
    """
    attrs: Dict[str, str] = {}
    if model:
        attrs["llm.model_name"] = truncate_string(model, _ID_MAX)
        attrs["gen_ai.request.model"] = truncate_string(model, _ID_MAX)
    if provider:
        attrs["llm.provider"] = truncate_string(provider, _PROVIDER_MAX)
    attrs.update(_provider_attributes(gen_ai_provider or provider))
    return attrs


def _metric_model_labels(model: Any, provider: Any) -> Dict[str, str]:
    """``model`` / ``provider`` metric labels: truncated, absent when unknown."""
    labels: Dict[str, str] = {}
    if model:
        labels["model"] = truncate_string(model, _ID_MAX)
    if provider:
        labels["provider"] = truncate_string(provider, _PROVIDER_MAX)
    return labels


def _gen_ai_attributes(session_id: Optional[str], operation_name: str) -> Dict[str, str]:
    """``gen_ai.operation.name`` plus ``gen_ai.conversation.id`` when there is a session."""
    attrs: Dict[str, str] = {"gen_ai.operation.name": operation_name}
    if session_id:
        attrs["gen_ai.conversation.id"] = truncate_string(session_id, _ID_MAX)
    return attrs


def _weave_turn_attributes(
    session_id: Optional[str],
    extra_kwargs: Optional[dict] = None,
) -> Dict[str, Any]:
    """Attributes that make raw OTLP spans render cleanly in Weave's Agents UI."""
    kwargs = extra_kwargs or {}
    agent_name = truncate_string(kwargs.get("agent_name") or _DEFAULT_AGENT_NAME, _ID_MAX)
    attrs: Dict[str, Any] = {
        "gen_ai.agent.name": agent_name,
        "wandb.is_turn": True,
    }
    if session_id:
        attrs["wandb.thread_id"] = truncate_string(session_id, _ID_MAX)
    version = package_version()
    if version:
        attrs["weave.agent.version"] = version
    return attrs


# ── Correlation ──────────────────────────────────────────────────────────────


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
        value = truncate_string(raw, _ID_MAX)
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

    Read-only: this runs on every hook, including ones that name sessions that
    never start a span here (a delegation's parent id, an API error), so it
    must not create aggregators (#105). ``_start_session_span`` is the one
    writer of ``correlation_id``.
    """
    incoming = _extract_correlation_id(extra_kwargs)
    session_key = str(session_id) if session_id else ""
    correlation_id = incoming

    if session_key:
        ps = tracer.sessions.peek(session_key)
        if incoming:
            if ps is not None:
                ps.correlation_id = incoming
        elif ps is not None and ps.correlation_id:
            correlation_id = ps.correlation_id
        else:
            correlation_id = truncate_string(session_id, _ID_MAX)

    if not correlation_id:
        return {}
    return {"correlation.id": truncate_string(correlation_id, _ID_MAX)}


# ── Sender / turn ────────────────────────────────────────────────────────────


def _sender_attributes(sender_id: str, platform: str) -> Dict[str, str]:
    """Return backend-neutral sender attributes for trace/user filtering."""
    if not sender_id:
        return {}
    attrs = {"hermes.sender.id": sender_id}
    attrs["user.id"] = f"{platform}:{sender_id}" if platform else sender_id
    return attrs


def _per_session_sender_attributes(ps: Any) -> Dict[str, str]:
    """Return sender attributes from a PerSession aggregator."""
    if ps is None or not ps.sender_id:
        return {}
    attrs = {"hermes.sender.id": ps.sender_id}
    if ps.user_id:
        attrs["user.id"] = ps.user_id
    return attrs


def _session_sender_attributes(tracer, session_id: Optional[str]) -> Dict[str, str]:
    """Return sender attributes already captured for a session."""
    if not session_id:
        return {}
    return _per_session_sender_attributes(tracer.sessions.peek(session_id))


def _turn_attributes(tracer, session_id: Optional[str]) -> Dict[str, int]:
    """Return ``hermes.turn.number`` for the session's current user turn.

    Empty until ``pre_llm_call`` has numbered the turn, so spans that can fire
    before it (the session root on ``on_session_start``) simply omit it.
    """
    turn = tracer.sessions.turn_number(session_id) if session_id else 0
    return {"hermes.turn.number": turn} if turn else {}


def _session_context_attributes(tracer, session_id: Optional[str], kwargs: dict) -> Dict[str, Any]:
    """The per-session context every child span carries: correlation, sender, turn number."""
    if not session_id:
        return {}
    attrs: Dict[str, Any] = {}
    attrs.update(_correlation_attributes(tracer, session_id, kwargs))
    attrs.update(_session_sender_attributes(tracer, session_id))
    attrs.update(_turn_attributes(tracer, session_id))
    return attrs


# ── Turn summary ─────────────────────────────────────────────────────────────


def _summary_attributes(summary: TurnSummary) -> Dict[str, Any]:
    """Convert a TurnSummary into hermes.turn.* attribute dict."""
    attrs: Dict[str, Any] = {}
    if summary.tool_names:
        attrs["hermes.turn.tool_count"] = len(summary.tool_names)
        attrs["hermes.turn.tools"] = clip_joined(sorted(summary.tool_names), ",")
    if summary.tool_targets:
        attrs["hermes.turn.tool_targets"] = clip_joined(summary.tool_targets, "|")
    if summary.tool_commands:
        attrs["hermes.turn.tool_commands"] = clip_joined(summary.tool_commands, "|")
    if summary.tool_outcomes:
        attrs["hermes.turn.tool_outcomes"] = clip_joined(sorted(summary.tool_outcomes), ",")
    if summary.skill_names:
        attrs["hermes.turn.skill_count"] = len(summary.skill_names)
        attrs["hermes.turn.skills"] = clip_joined(sorted(summary.skill_names), ",")
    if summary.api_call_count:
        attrs["hermes.turn.api_call_count"] = summary.api_call_count
    if summary.final_status:
        attrs["hermes.turn.final_status"] = summary.final_status
    return attrs


# ── Host utilization ─────────────────────────────────────────────────────────


def _tool_host_utilization_attributes(tracer, started_at: float, ended_at: float) -> Dict[str, Any]:
    """Average / peak host utilization while a tool ran (``host_metrics`` only).

    Slices the in-memory ring of the host-metrics sampler by the tool's
    ``perf_counter`` window. CPU is the Hermes process tree, so it is
    attributable to the tool (and whatever it spawned); GPU is the whole
    host's busy ratio during the window — coincident load, not attribution,
    unless the tool itself drives the GPU. Empty when host metrics are off or
    the tool finished between two samples.
    """
    sampler = tracer.host_metrics
    if sampler is None:
        return {}
    try:
        stats = sampler.window(started_at, ended_at)
    except Exception:  # pragma: no cover — never break the hook
        return {}
    if stats is None:
        return {}
    attrs: Dict[str, Any] = {
        "hermes.tool.cpu.utilization.avg": round(stats.cpu_avg, 4),
        "hermes.tool.cpu.utilization.peak": round(stats.cpu_peak, 4),
    }
    if stats.gpu_avg is not None:
        attrs["hermes.tool.gpu.utilization.avg"] = round(stats.gpu_avg, 4)
        attrs["hermes.tool.gpu.utilization.peak"] = round(stats.gpu_peak or 0.0, 4)
    return attrs


# ── Messages / request parameters ────────────────────────────────────────────


def _message_json(role: str, content: Any) -> Optional[str]:
    """One-message ``gen_ai.*.messages`` payload, or None for empty content."""
    if content is None or content == "":
        return None
    try:
        return json.dumps([{"role": role, "content": str(content)}], ensure_ascii=False)
    except (TypeError, ValueError):
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


def _gen_ai_request_param_attributes(kwargs: dict) -> Dict[str, Any]:
    """Best-effort standard GenAI request parameter attributes."""
    attrs: Dict[str, Any] = {}
    for src, dest in (
        ("temperature", "gen_ai.request.temperature"),
        ("top_p", "gen_ai.request.top_p"),
        ("frequency_penalty", "gen_ai.request.frequency_penalty"),
        ("presence_penalty", "gen_ai.request.presence_penalty"),
    ):
        value = optional_number(kwargs.get(src))
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
    reasoning = truncate_string(reasoning, _PROVIDER_MAX) if reasoning is not None else ""
    if reasoning:
        attrs["gen_ai.request.reasoning.level"] = reasoning

    stop_sequences = kwargs.get("stop_sequences") or kwargs.get("stop")
    if isinstance(stop_sequences, str):
        attrs["gen_ai.request.stop_sequences"] = [stop_sequences]
    elif isinstance(stop_sequences, (list, tuple)) and stop_sequences:
        attrs["gen_ai.request.stop_sequences"] = [
            truncate_string(v, _ID_MAX) for v in stop_sequences
        ]

    choice_count = kwargs.get("choice_count") or kwargs.get("n")
    if choice_count is not None and not isinstance(choice_count, bool):
        try:
            n = int(choice_count)
            if n != 1:
                attrs["gen_ai.request.choice.count"] = n
        except (TypeError, ValueError):
            pass
    return attrs
