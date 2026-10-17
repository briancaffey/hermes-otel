"""Attribute builders shared by the hook callbacks.

Every span attribute that more than one hook emits is built here, once, so the
conventions (dual OpenInference + OTel GenAI spelling, truncation limits,
identity keys) cannot drift between hooks (#104).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional, Tuple

from ..coerce import optional_number
from ..helpers import (
    DEFAULT_SERVICE_NAME,
    clip_joined,
    named_profile,
    package_version,
    truncate_string,
)
from ..session_state import TurnSummary

_DEFAULT_AGENT_NAME = DEFAULT_SERVICE_NAME

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


# Hermes provider id -> (OTel GenAI ``gen_ai.provider.name``, OpenInference
# ``llm.provider``) when the conventions have a well-known spelling for it
# (#475). Ids with no entry pass through unchanged (``openrouter``, ``nvidia``,
# ``custom``, ``lmstudio`` …): the conventions name no value for them, and an
# invented one would be worse than the raw id. The raw id always stays in
# ``hermes.provider``. Sources: ``GenAiProviderNameValues`` in
# opentelemetry-semantic-conventions, ``OpenInferenceLLMProviderValues`` in
# openinference-semantic-conventions, ``PROVIDER_REGISTRY`` in Hermes.
PROVIDER_CANONICAL: Dict[str, Tuple[Optional[str], Optional[str]]] = {
    "anthropic": ("anthropic", "anthropic"),
    "openai": ("openai", "openai"),
    "openai-api": ("openai", "openai"),
    "openai-codex": ("openai", "openai"),
    "xai": ("x_ai", "xai"),
    "xai-oauth": ("x_ai", "xai"),
    "deepseek": ("deepseek", "deepseek"),
    "bedrock": ("aws.bedrock", "aws"),
    "vertex": ("gcp.vertex_ai", "google"),
    "gemini": ("gcp.gemini", "google"),
    "azure-foundry": ("azure.ai.inference", "azure"),
    "groq": ("groq", "groq"),
    "cohere": ("cohere", "cohere"),
    "perplexity": ("perplexity", "perplexity"),
    "mistral": ("mistral_ai", "mistralai"),
    "minimax": (None, "minimax"),
    "minimax-cn": (None, "minimax"),
    "minimax-oauth": (None, "minimax"),
    "kimi-coding": (None, "moonshot"),
    "kimi-coding-cn": (None, "moonshot"),
    "zai": (None, "zai"),
    "ollama-cloud": (None, "ollama"),
}


def canonical_provider(provider: Any) -> Tuple[str, str, str]:
    """``(raw, gen_ai, openinference)`` for a Hermes provider id; empty strings when unset.

    ``gen_ai`` is the value for ``gen_ai.provider.name`` / ``gen_ai.system``
    and the ``provider`` metric label, ``openinference`` the value for
    ``llm.provider``; each is the raw id when the convention names nothing.
    """
    raw = truncate_string(provider, _PROVIDER_MAX) if provider else ""
    if not raw:
        return "", "", ""
    gen_ai, oi = PROVIDER_CANONICAL.get(raw.strip().lower(), (None, None))
    return raw, gen_ai or raw, oi or raw


def _provider_attributes(provider: Any) -> Dict[str, str]:
    """Return current and compatibility provider attributes."""
    raw, gen_ai, _oi = canonical_provider(provider)
    if not raw:
        return {}
    return {
        "gen_ai.provider.name": gen_ai,
        # Kept for older OTel drafts and existing dashboards.
        "gen_ai.system": gen_ai,
        # The Hermes id as configured, whatever the conventions call it.
        "hermes.provider": raw,
    }


def _model_attributes(model: Any, provider: Any = None) -> Dict[str, str]:
    """Model / provider identity in both conventions, from real data only.

    ``llm.model_name`` + ``gen_ai.request.model`` when the model is known;
    ``llm.provider`` + the ``gen_ai.provider.name`` / ``gen_ai.system`` pair only
    when ``provider`` is the actual LLM provider (``openrouter``, ``anthropic``
    …). The Hermes *platform* (``cli``, ``telegram``) is never a provider; it
    goes under ``hermes.platform`` via :func:`_platform_attributes` (#153).
    """
    attrs: Dict[str, str] = {}
    if model:
        attrs["llm.model_name"] = truncate_string(model, _ID_MAX)
        attrs["gen_ai.request.model"] = truncate_string(model, _ID_MAX)
    if provider:
        attrs["llm.provider"] = canonical_provider(provider)[2]
        attrs.update(_provider_attributes(provider))
    return attrs


def session_env(name: str) -> str:
    """A Hermes session variable: the gateway's per-turn context var when Hermes
    is importable (it falls back to the environment itself), else ``os.environ``."""
    try:
        from gateway.session_context import get_session_env  # type: ignore[import-not-found]

        return str(get_session_env(name) or "")
    except Exception:
        import os

        return os.environ.get(name, "")


# ``hermes.turn.trigger`` (#394): what started a turn, from what Hermes makes
# observable at turn start (the hook's ``platform``, the session-context vars
# and the process environment). A closed set, so a backend can group by it.
TURN_TRIGGERS = (
    "user",  # a person: CLI / TUI / desktop / a gateway platform / ACP
    "cron",  # a scheduled cron job
    "kanban",  # a kanban worker run (HERMES_KANBAN_TASK)
    "subagent",  # a delegated child agent
    "a2a",  # an inbound agent-to-agent call
    "api",  # the API server (/v1/runs, /v1/chat/completions)
    "oneshot",  # a single-query run (hermes -z, chat -q) not covered above
    "curator",  # Hermes's background curator
    "batch",  # the batch runner
    "system",  # other host housekeeping (gateway hygiene)
)
_PLATFORM_TRIGGERS = {
    "subagent": "subagent",
    "cron": "cron",
    "a2a": "a2a",
    "api_server": "api",
    "curator": "curator",
    "batch": "batch",
    "gateway_hygiene": "system",
}


def turn_trigger(
    platform: Any, *, is_subagent: bool = False, env: Callable[[str], str] = lambda _n: ""
) -> str:
    """Classify what started a turn into one of :data:`TURN_TRIGGERS`.

    ``env(name)`` reads a Hermes session variable (context var first, then the
    process environment). ``/loop``, ``/heartbeat`` and ``/goal`` re-entries
    and bot-to-bot DMs are not observable as such at turn start (only as a
    message prefix), so they classify as the turn they ride on.
    """
    p = str(platform or "").strip().lower()
    if is_subagent or p == "subagent":
        return "subagent"
    if p == "cron" or env("HERMES_CRON_SESSION").strip() in ("1", "true"):
        return "cron"
    if env("HERMES_KANBAN_TASK").strip():
        return "kanban"
    if p in _PLATFORM_TRIGGERS:
        return _PLATFORM_TRIGGERS[p]
    if env("HERMES_SESSION_SOURCE").strip().lower() == "a2a":
        return "a2a"
    if env("HERMES_SINGLE_QUERY_SESSION").strip() in ("1", "true"):
        return "oneshot"
    return "user"


def turn_trigger_attributes(platform: Any, is_subagent: bool = False) -> Dict[str, str]:
    """``hermes.turn.trigger``: what started the turn (#394), see
    :func:`turn_trigger`."""
    return {"hermes.turn.trigger": turn_trigger(platform, is_subagent=is_subagent, env=session_env)}


def configured_default_model() -> str:
    """The profile's configured main model (``model.default`` in Hermes's
    config.yaml), or ``""`` when Hermes is not importable or names none."""
    try:
        from hermes_cli.config import read_raw_config_readonly  # type: ignore[import-not-found]

        cfg = read_raw_config_readonly()
    except Exception:
        return ""
    m = cfg.get("model") if isinstance(cfg, dict) else None
    if isinstance(m, str):
        return m.strip()
    if isinstance(m, dict):
        return str(m.get("default") or m.get("model") or m.get("name") or "").strip()
    return ""


def model_override_attributes(model: Any) -> Dict[str, bool]:
    """``hermes.agent.model_override``: whether the turn runs on a model other
    than the profile's configured default (a ``delegation.model`` child, a
    kanban ``--model`` worker, a cron job's own model, ``-m`` or ``/model``).
    Absent when either side is unknown (#391)."""
    default = configured_default_model()
    text = str(model or "").strip()
    if not default or not text:
        return {}
    return {"hermes.agent.model_override": text != default}


def profile_attributes(tracer: Any) -> Dict[str, str]:
    """``hermes.profile``: the Hermes profile this plugin instance serves (#70)."""
    name = getattr(tracer, "profile_name", None)
    return {"hermes.profile": truncate_string(str(name), _PROVIDER_MAX)} if name else {}


def _platform_attributes(platform: Any) -> Dict[str, str]:
    """``hermes.platform``: the Hermes surface a turn ran on (``cli``, ``telegram`` …)."""
    if not platform:
        return {}
    return {"hermes.platform": truncate_string(platform, _PROVIDER_MAX)}


def _response_model_attributes(response_model: Any) -> Dict[str, str]:
    """``gen_ai.response.model`` only when a provider actually reported one (#155)."""
    if not response_model:
        return {}
    return {"gen_ai.response.model": truncate_string(response_model, _ID_MAX)}


def _metric_model_labels(model: Any, provider: Any) -> Dict[str, str]:
    """``model`` / ``provider`` metric labels: truncated, absent when unknown."""
    labels: Dict[str, str] = {}
    if model:
        labels["model"] = truncate_string(model, _ID_MAX)
    if provider:
        # The same spelling as gen_ai.provider.name on the spans (#475).
        labels["provider"] = canonical_provider(provider)[1]
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
    profile: Any = None,
) -> Dict[str, Any]:
    """Attributes that make raw OTLP spans render cleanly in Weave's Agents UI.

    ``gen_ai.agent.name`` is the host's ``agent_name`` when it passes one, else
    the active named profile (the closest thing Hermes has to a stable agent
    name, #395), else ``hermes-agent``.
    """
    kwargs = extra_kwargs or {}
    agent_name = truncate_string(
        kwargs.get("agent_name") or named_profile(profile) or _DEFAULT_AGENT_NAME, _ID_MAX
    )
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


def correlation_attributes(tracer, session_id: Optional[str], extra_kwargs: dict) -> Dict[str, str]:
    """``correlation.id`` for a hook callback, only when a host supplied one.

    Preference order:
    1. Incoming correlation ID supplied by the host app/hook kwargs.
    2. The correlation ID already recorded for the session (so every span of
       a session carries the id the host passed at its start).

    There is no session-id fallback: Hermes 0.21 passes no correlation id on
    any hook, and a copy of ``session.id`` under this name read as an upstream
    request identity that never existed (#154). ``session.id`` /
    ``gen_ai.conversation.id`` already group a session's spans.

    Read-only: this runs on every hook, including ones that name sessions that
    never start a span here (a delegation's parent id, an API error), so it
    must not create aggregators (#105). ``_start_session_span`` is the one
    writer of ``correlation_id``.
    """
    correlation_id = _extract_correlation_id(extra_kwargs)
    if not correlation_id and session_id:
        ps = tracer.sessions.peek(str(session_id))
        if ps is not None and ps.correlation_id:
            correlation_id = ps.correlation_id
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
    attrs.update(correlation_attributes(tracer, session_id, kwargs))
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
