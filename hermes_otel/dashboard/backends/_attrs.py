"""The plugin's attribute names and the underscored spellings backends store them in.

OpenObserve, Uptrace and Loki flatten dotted attribute keys with underscores
(``llm.model_name`` -> ``llm_model_name``); the only way to give such a column
its real name back is a table of every attribute the plugin emits (#158).
Shared here so no adapter imports another adapter for it.
"""

from __future__ import annotations

from typing import Dict


def oo_col(dotted_name: str) -> str:
    """The underscored column name a dotted attribute key is stored under."""
    return dotted_name.replace(".", "_")


# Every attribute name the plugin emits (the ``span-attributes.md`` reference
# plus the OTel / OpenInference standard keys it sets). OpenObserve flattens
# ``a.b_c`` and ``a.b.c`` to the same ``a_b_c`` column, so the only way to give
# a column its real name back is a table; ``tests/unit/test_openobserve_columns.py``
# fails when the docs list a name that is missing here (#158).
KNOWN_ATTRIBUTES = (
    "code.file.path",
    "code.filepath",
    "code.function",
    "code.function.name",
    "code.line.number",
    "code.lineno",
    "correlation.id",
    "error.message",
    "error.type",
    "exception.escaped",
    "exception.message",
    "exception.stacktrace",
    "exception.type",
    "gen_ai.agent.name",
    "gen_ai.conversation.id",
    "gen_ai.input.messages",
    "gen_ai.operation.name",
    "gen_ai.output.messages",
    "gen_ai.provider.name",
    "gen_ai.request.choice.count",
    "gen_ai.request.frequency_penalty",
    "gen_ai.request.max_tokens",
    "gen_ai.request.model",
    "gen_ai.request.presence_penalty",
    "gen_ai.request.reasoning.level",
    "gen_ai.request.stop_sequences",
    "gen_ai.request.stream",
    "gen_ai.request.temperature",
    "gen_ai.request.top_k",
    "gen_ai.request.top_p",
    "gen_ai.response.finish_reasons",
    "gen_ai.response.id",
    "gen_ai.response.model",
    "gen_ai.response.status_code",
    "gen_ai.skill.name",
    "gen_ai.system",
    "gen_ai.system_instructions",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.id",
    "gen_ai.tool.call.result",
    "gen_ai.tool.name",
    "gen_ai.tool.type",
    "gen_ai.usage.cache_creation.input_tokens",
    "gen_ai.usage.cache_creation_input_tokens",
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_read_input_tokens",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.reasoning.output_tokens",
    "gen_ai.usage.total_tokens",
    "hermes.api.error",
    "hermes.approval.choice",
    "hermes.approval.command",
    "hermes.approval.decided_by",
    "hermes.approval.decision",
    "hermes.approval.description",
    "hermes.approval.duration_ms",
    "hermes.approval.granted",
    "hermes.approval.pattern_key",
    "hermes.approval.pattern_keys",
    "hermes.approval.surface",
    "hermes.approval.timed_out",
    "hermes.content.input_chars",
    "hermes.content.output_chars",
    "hermes.conversation.message_count",
    "hermes.cost.source",
    "hermes.cost.status",
    "hermes.cost.usage",
    "hermes.cron.job_id",
    "hermes.link",
    "hermes.log.attribution",
    "hermes.max_retries",
    "hermes.platform",
    "hermes.preview.input.original_chars",
    "hermes.preview.input.truncated",
    "hermes.preview.output.original_chars",
    "hermes.preview.output.truncated",
    "hermes.profile",
    "hermes.retry.count",
    "hermes.retryable",
    "hermes.sender.id",
    "hermes.session.completed",
    "hermes.session.duration_s",
    "hermes.session.failed",
    "hermes.session.finalize_reason",
    "hermes.session.interrupted",
    "hermes.session.is_subagent",
    "hermes.session.kind",
    "hermes.session.previous_id",
    "hermes.session.reset_reason",
    "hermes.session.synthesized",
    "hermes.session.turn_count",
    "hermes.session_id",
    "hermes.skill.name",
    "hermes.skill.path",
    "hermes.skill.result_status",
    "hermes.skill.source",
    "hermes.span_kind",
    "hermes.subagent.child_id",
    "hermes.subagent.child_session_id",
    "hermes.subagent.duration_ms",
    "hermes.subagent.goal",
    "hermes.subagent.parent_id",
    "hermes.subagent.parent_session_id",
    "hermes.subagent.parent_turn_id",
    "hermes.subagent.role",
    "hermes.subagent.status",
    "hermes.subagent.summary",
    "hermes.tool.blocked_by",
    "hermes.tool.command",
    "hermes.tool.cpu.utilization.avg",
    "hermes.tool.cpu.utilization.peak",
    "hermes.tool.decided_by",
    "hermes.tool.duration_s",
    "hermes.tool.gpu.utilization.avg",
    "hermes.tool.gpu.utilization.peak",
    "hermes.tool.name",
    "hermes.tool.outcome",
    "hermes.tool.target",
    "hermes.turn.api_call_count",
    "hermes.turn.duration_s",
    "hermes.turn.exit_reason",
    "hermes.turn.final_status",
    "hermes.turn.number",
    "hermes.turn.skill_count",
    "hermes.turn.skills",
    "hermes.turn.tool_commands",
    "hermes.turn.tool_count",
    "hermes.turn.tool_outcomes",
    "hermes.turn.tool_targets",
    "hermes.turn.tools",
    "host.name",
    "http.response.status_code",
    "input.mime_type",
    "input.value",
    "llm.api_mode",
    "llm.input_messages",
    "llm.model_name",
    "llm.output.content",
    "llm.output.tool_calls",
    "llm.provider",
    "llm.request.approx_input_tokens",
    "llm.request.max_tokens",
    "llm.request.message_count",
    "llm.response.duration_ms",
    "llm.response.finish_reason",
    "llm.response.output_chars",
    "llm.response.tool_calls",
    "llm.system_prompt",
    "llm.token_count.completion",
    "llm.token_count.completion_details.reasoning",
    "llm.token_count.prompt",
    "llm.token_count.prompt_details.cache_read",
    "llm.token_count.prompt_details.cache_write",
    "llm.token_count.total",
    "openinference.project.name",
    "openinference.span.kind",
    "output.mime_type",
    "output.value",
    "process.pid",
    "service.instance.id",
    "service.name",
    "service.version",
    "session.id",
    "telemetry.sdk.language",
    "telemetry.sdk.name",
    "telemetry.sdk.version",
    "tool.name",
    "traceloop.span.kind",
    "user.id",
    "wandb.entity",
    "wandb.is_turn",
    "wandb.project",
    "wandb.thread_id",
    "weave.agent.version",
)


def _build_column_table() -> Dict[str, str]:
    table: Dict[str, str] = {}
    # Two names can flatten to one column (``gen_ai.usage.cache_read.input_tokens``
    # and its legacy alias ``gen_ai.usage.cache_read_input_tokens``; ``session.id``
    # and ``session_id``): keep the current dotted spelling, i.e. the one with
    # more dots, and on a tie the shorter name.
    for attr in sorted(KNOWN_ATTRIBUTES, key=lambda a: (-a.count("."), len(a), a)):
        table.setdefault(oo_col(attr), attr)
    return table


_COLUMN_TO_ATTRIBUTE = _build_column_table()


def dotted(underscored: str) -> str:
    """The attribute name behind an OpenObserve column, or the column name itself.

    Not a rule: ``llm_model_name`` is ``llm.model_name`` and
    ``gen_ai_usage_input_tokens`` is ``gen_ai.usage.input_tokens``, which no
    underscore-to-dot rewrite can recover (the old one produced
    ``llm.model.name``). Unknown columns keep OpenObserve's own name rather
    than an invented one.
    """
    return _COLUMN_TO_ATTRIBUTE.get(underscored, underscored)
