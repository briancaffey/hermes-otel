"""Structured events the plugin emits from the Hermes hooks (#267).

An event is an OTel log record with ``event_name`` set, emitted through
``Logger.emit`` on the plugin's own instrumentation scope (``hermes_otel``,
version = the plugin version), never through the stdlib ``logging`` bridge. So
events work with ``logs.capture: false`` as long as a log-capable backend or
the live store exists, and ``logs.events.enabled`` (default off) is the only
switch. Each event carries the same attribute names as the span it mirrors
(``reference/span-attributes.md``), the turn's trace context, and the session
attributes; the enrichment processor then redacts it like any other record.

:data:`EVENTS` is the catalogue the docs are generated from (Phase 5, #269):
one entry per event with the hook that emits it, its severity rule and the
attributes it may carry. Keep it in step with the emit sites in ``hooks/``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from . import semconv as _sc
from .helpers import clip_preview

SCOPE_NAME = "hermes_otel"

# Event names (the hooks import these; the registry in ``semconv`` owns them, #404).
TURN_START = _sc.EVENT_TURN_START
TURN_END = _sc.EVENT_TURN_END
TOOL_CALL = _sc.EVENT_TOOL_CALL
APPROVAL_DECISION = _sc.EVENT_APPROVAL_DECISION
API_ERROR = _sc.EVENT_API_ERROR
SUBAGENT_START = _sc.EVENT_SUBAGENT_START
SUBAGENT_STOP = _sc.EVENT_SUBAGENT_STOP
SESSION_FINALIZE = _sc.EVENT_SESSION_FINALIZE
KANBAN_TRANSITION = _sc.EVENT_KANBAN_TRANSITION
AUXILIARY_CALL = _sc.EVENT_AUXILIARY_CALL
INFERENCE_DETAILS = _sc.EVENT_INFERENCE_DETAILS
COMMIT = _sc.EVENT_COMMIT

# Event-only attributes (the spans carry *_ms; events report seconds per semconv).
ATTR_TOOL_DURATION_S = "hermes.tool.duration_s"
ATTR_TURN_DURATION_S = "hermes.turn.duration_s"
ATTR_COMMIT_SUBJECT = "hermes.commit.subject"

# Attribute-name prefixes each event copies from its span's attribute dict.
TOOL_PREFIXES = ("gen_ai.tool.", "hermes.tool.")
APPROVAL_PREFIXES = ("hermes.approval.", "gen_ai.tool.")
SUBAGENT_PREFIXES = ("hermes.subagent.",)
KANBAN_PREFIXES = ("hermes.kanban.",)
TURN_END_PREFIXES = ("hermes.turn.", "gen_ai.usage.", "hermes.cost.", "hermes.session.")
TURN_END_KEYS = ("error.type", "hermes.platform", "gen_ai.request.model", "gen_ai.provider.name")
INFERENCE_PREFIXES = ("gen_ai.", "hermes.cost.")
INFERENCE_KEYS = ("input.value", "output.value")

# Attributes that carry prompt, response or tool content. They are the only
# ones ``logs.events.content`` governs: ``full`` keeps them as the span has
# them, ``preview`` clips them, ``off`` drops them. Everything else on an event
# is metadata (ids, counts, statuses, durations) and always travels.
CONTENT_ATTRIBUTES: Tuple[str, ...] = (
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.system_instructions",
    "gen_ai.tool.definitions",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    "input.value",
    "output.value",
    "hermes.subagent.goal",
    "hermes.subagent.summary",
    "hermes.kanban.summary",
    "hermes.kanban.reason",
    "hermes.commit.subject",
)
CONTENT_MODES: Tuple[str, ...] = ("off", "preview", "full")
_CONTENT_RANK = {"off": 0, "preview": 1, "full": 2}

EVENTS: Dict[str, Dict[str, Any]] = {
    "hermes.turn.start": {
        "hook": "pre_llm_call",
        "severity": "INFO",
        "body": "turn <n> started",
        "attributes": (
            "hermes.session_id",
            "gen_ai.conversation.id",
            "hermes.platform",
            "gen_ai.request.model",
            "gen_ai.provider.name",
            "hermes.turn.number",
        ),
    },
    "hermes.turn.end": {
        "hook": "on_session_end",
        "severity": "INFO; WARN when interrupted; ERROR when failed",
        "body": "turn <n> <final_status>",
        "attributes": (
            "hermes.turn.number",
            "hermes.turn.final_status",
            "hermes.turn.exit_reason",
            "hermes.turn.api_call_count",
            "hermes.turn.tool_count",
            "hermes.turn.tools",
            "gen_ai.usage.input_tokens",
            "gen_ai.usage.output_tokens",
            "gen_ai.usage.cache_read.input_tokens",
            "gen_ai.usage.reasoning.output_tokens",
            "hermes.cost.usage",
            "hermes.turn.duration_s",
            "error.type",
        ),
    },
    "hermes.tool.call": {
        "hook": "post_tool_call",
        "severity": "INFO; WARN when blocked or timed out; ERROR on error",
        "body": "tool <name> <outcome>",
        "attributes": (
            "gen_ai.tool.name",
            "gen_ai.tool.call.id",
            "gen_ai.tool.type",
            "hermes.tool.outcome",
            "hermes.tool.decided_by",
            "hermes.tool.duration_s",
            "gen_ai.tool.call.arguments (content)",
            "gen_ai.tool.call.result (content)",
        ),
    },
    "hermes.approval.decision": {
        "hook": "post_approval_response",
        "severity": "INFO; WARN when denied or timed out",
        "body": "approval <choice> for <tool>",
        "attributes": (
            "hermes.approval.choice",
            "hermes.approval.granted",
            "hermes.approval.decided_by",
            "hermes.approval.surface",
            "hermes.approval.timed_out",
            "hermes.approval.duration_ms",
            "gen_ai.tool.name",
        ),
    },
    "hermes.kanban.transition": {
        "hook": "kanban_task_claimed / on_kanban_worker_spawned / on_kanban_worker_exited / on_kanban_worker_stale_claim / kanban_task_completed / kanban_task_blocked",
        "severity": "INFO; WARN when blocked or a claim went stale; ERROR when the worker crashed or was killed",
        "body": "kanban <outcome> <task_id>",
        "attributes": (
            "hermes.kanban.event",
            "hermes.kanban.task_id",
            "hermes.kanban.board",
            "hermes.kanban.assignee",
            "hermes.kanban.run_id",
            "hermes.kanban.worker_pid",
            "hermes.kanban.exit_kind",
            "hermes.kanban.exit_code",
            "hermes.kanban.retry_status",
            "hermes.kanban.heartbeat_stale",
            "hermes.kanban.outcome",
            "hermes.kanban.summary",
            "hermes.kanban.reason",
        ),
    },
    "hermes.api.error": {
        "hook": "api_request_error",
        "severity": "ERROR; WARN when retryable",
        "body": "api error <error.type> from <provider>",
        "attributes": (
            "error.type",
            "http.response.status_code",
            "hermes.retryable",
            "hermes.retry.count",
            "gen_ai.request.model",
            "gen_ai.provider.name",
            "exception.type",
            "exception.message",
        ),
    },
    "hermes.subagent.start": {
        "hook": "subagent_start",
        "severity": "INFO",
        "body": "sub-agent <role> started",
        "attributes": (
            "hermes.subagent.role",
            "hermes.subagent.child_session_id",
            "hermes.subagent.parent_session_id",
            "hermes.subagent.goal (content)",
        ),
    },
    "hermes.subagent.stop": {
        "hook": "subagent_stop",
        "severity": "INFO; ERROR when the sub-agent reports failure",
        "body": "sub-agent <role> <status>",
        "attributes": (
            "hermes.subagent.role",
            "hermes.subagent.status",
            "hermes.subagent.duration_ms",
            "hermes.subagent.child_session_id",
            "hermes.subagent.parent_session_id",
            "hermes.subagent.summary (content)",
        ),
    },
    "hermes.session.finalize": {
        "hook": "on_session_finalize / on_session_reset",
        "severity": "INFO",
        "body": "session <id> <finalized|reset> (<reason>): <n> turn(s)",
        "attributes": (
            "hermes.session.turn_count",
            "hermes.session.duration_s",
            "hermes.session.finalize_reason / hermes.session.reset_reason",
        ),
    },
    "hermes.auxiliary.call": {
        "hook": "post_auxiliary_call",
        "severity": "INFO; ERROR when the provider attempt failed",
        "body": "auxiliary <task> <model> (<input>/<output> tokens)",
        "attributes": (
            "hermes.auxiliary.task",
            "hermes.query_source",
            "gen_ai.request.model",
            "gen_ai.provider.name",
            "gen_ai.response.model",
            "gen_ai.usage.input_tokens",
            "gen_ai.usage.output_tokens",
            "hermes.cost.usage",
            "llm.response.duration_ms",
            "error.type",
        ),
    },
    "commit": {
        "hook": "post_tool_call",
        "severity": "INFO",
        "body": "commit",
        "attributes": (
            "gen_ai.tool.name",
            "gen_ai.tool.call.id",
            "hermes.commit.subject (content)",
        ),
    },
    "gen_ai.client.inference.operation.details": {
        "hook": "post_api_request",
        "severity": "INFO",
        "body": "<operation> <model> (<input>/<output> tokens)",
        "attributes": (
            "gen_ai.operation.name",
            "gen_ai.provider.name",
            "gen_ai.request.model",
            "gen_ai.response.model",
            "gen_ai.response.id",
            "gen_ai.response.finish_reasons",
            "gen_ai.usage.input_tokens",
            "gen_ai.usage.output_tokens",
            "gen_ai.conversation.id",
            "gen_ai.input.messages (content)",
            "gen_ai.output.messages (content)",
            "gen_ai.system_instructions (content)",
        ),
    },
}


def resolve_content_mode(events_content: str, span_content_mode: str) -> str:
    """``inherit`` follows the span content mode; anything else is itself (validated at load)."""
    mode = (events_content or "inherit").lower()
    if mode == "inherit":
        return span_content_mode if span_content_mode in CONTENT_MODES else "full"
    return mode if mode in CONTENT_MODES else "full"


def narrower(a: str, b: str) -> str:
    """The stricter of two content modes."""
    return a if _CONTENT_RANK.get(a, 2) <= _CONTENT_RANK.get(b, 2) else b


def apply_content_mode(
    attributes: Mapping[str, Any], mode: str, preview_chars: int
) -> Dict[str, Any]:
    """A copy of *attributes* with the content attributes gated by *mode*.

    ``full`` keeps them, ``preview`` clips string values to *preview_chars*,
    ``off`` removes them. Metadata attributes are untouched.
    """
    out: Dict[str, Any] = {}
    for key, value in attributes.items():
        if key in CONTENT_ATTRIBUTES:
            if mode == "off":
                continue
            if mode == "preview" and isinstance(value, str):
                value = clip_preview(value, preview_chars)
        out[key] = value
    return out


def has_content(attributes: Mapping[str, Any]) -> bool:
    return any(k in attributes for k in CONTENT_ATTRIBUTES)


def strip_none(
    attributes: Mapping[str, Any], keep: Optional[Iterable[str]] = None
) -> Dict[str, Any]:
    """Drop ``None`` values (the OTel log attribute model has no null)."""
    allowed = set(keep) if keep is not None else None
    return {
        k: v for k, v in attributes.items() if v is not None and (allowed is None or k in allowed)
    }
