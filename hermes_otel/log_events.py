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

from .helpers import clip_preview

SCOPE_NAME = "hermes_otel"

# Event names (the hooks import these so the names live in one place).
TURN_START = "hermes.turn.start"
TURN_END = "hermes.turn.end"
TOOL_CALL = "hermes.tool.call"
APPROVAL_DECISION = "hermes.approval.decision"
API_ERROR = "hermes.api.error"
SUBAGENT_START = "hermes.subagent.start"
SUBAGENT_STOP = "hermes.subagent.stop"
SESSION_FINALIZE = "hermes.session.finalize"
INFERENCE_DETAILS = "gen_ai.client.inference.operation.details"

# Event-only attributes (the spans carry *_ms; events report seconds per semconv).
ATTR_TOOL_DURATION_S = "hermes.tool.duration_s"
ATTR_TURN_DURATION_S = "hermes.turn.duration_s"

# Attribute-name prefixes each event copies from its span's attribute dict.
TOOL_PREFIXES = ("gen_ai.tool.", "hermes.tool.")
APPROVAL_PREFIXES = ("hermes.approval.", "gen_ai.tool.")
SUBAGENT_PREFIXES = ("hermes.subagent.",)
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
)
CONTENT_MODES: Tuple[str, ...] = ("off", "preview", "full")
_CONTENT_RANK = {"off": 0, "preview": 1, "full": 2}

# Severity text -> OTel severity number (the bottom of each band).
SEVERITY_NUMBERS = {"TRACE": 1, "DEBUG": 5, "INFO": 9, "WARN": 13, "ERROR": 17, "FATAL": 21}

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


def severity_number(text: str) -> int:
    return SEVERITY_NUMBERS.get(str(text).upper(), 9)


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
