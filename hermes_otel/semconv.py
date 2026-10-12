"""The plugin's telemetry vocabulary, in one place (#404).

Every attribute name the plugin emits, the fallback chains readers use to
find a value under either convention (OpenInference ``llm.*`` / ``input.value``
for Phoenix and OTel GenAI ``gen_ai.*`` for everything else), the span-kind
classification and the event names. Pure data: no OpenTelemetry import, so
the dashboard package, the query CLI, the scripts and the generator can all
import it.

What derives from this module:

* ``website/docs/reference/span-attributes.md`` is checked against
  :data:`ATTRIBUTES` (every registered name is on the page, and nothing
  else), by ``scripts/gen_semconv.py --check`` and ``tests/unit/test_semconv.py``.
* ``dashboard-ui/src/semconv.generated.ts`` carries the key chains and the
  span-kind table for ``lib.ts``, written by ``scripts/gen_semconv.py``.
* ``hermes_otel/dashboard/backends/_attrs.KNOWN_ATTRIBUTES`` is
  :data:`ATTRIBUTE_NAMES`.
* ``live_store.span_kind`` / ``_kind_sql`` and ``query_cli`` read the key
  chains and :data:`SPAN_KINDS` from here.

Adding an attribute: emit it in a hook, add its :class:`Attr` here, document
it on the reference page, run ``python scripts/gen_semconv.py`` and commit
the regenerated TypeScript. The hook literal is checked against the registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Attr:
    """One attribute the plugin may set.

    ``group`` is the registry's own grouping (session, usage, tool, ...);
    ``doc`` is a one-line meaning, taken from the reference page's row for the
    name; ``where`` names the reference-page section the attribute is first
    documented under (the span family, or ``Resource``).
    """

    name: str
    group: str
    doc: str = ""
    where: str = ""


ATTRIBUTES: Tuple[Attr, ...] = (
    # ── resource ──
    Attr(
        "host.name",
        "resource",
        "Hostname, only when `host_metrics: true`",
        "Resource (on every span)",
    ),
    Attr(
        "openinference.project.name",
        "resource",
        "`project_name` / `OTEL_PROJECT_NAME` (the Phoenix project); optional",
        "Resource (on every span)",
    ),
    Attr("process.pid", "resource", "Process id", "Resource (on every span)"),
    Attr(
        "service.instance.id",
        "resource",
        "UUID generated once per process — keeps two Hermes processes on different metric series",
        "Resource (on every span)",
    ),
    Attr(
        "service.name",
        "resource",
        "`hermes-agent` (fixed; override via `resource_attributes:`)",
        "Resource (on every span)",
    ),
    Attr(
        "service.version",
        "resource",
        "Plugin version, read from the shipped `plugin.yaml`",
        "Resource (on every span)",
    ),
    Attr("telemetry.sdk.language", "resource", "Set by the OTel SDK", "Resource (on every span)"),
    Attr("telemetry.sdk.name", "resource", "Set by the OTel SDK", "Resource (on every span)"),
    Attr("telemetry.sdk.version", "resource", "Set by the OTel SDK", "Resource (on every span)"),
    Attr(
        "wandb.entity",
        "resource",
        "W&B Weave routing (when configured)",
        "Resource (on every span)",
    ),
    Attr(
        "wandb.project",
        "resource",
        "W&B Weave routing (when configured)",
        "Resource (on every span)",
    ),
    # ── span_kind ──
    Attr(
        "hermes.span_kind",
        "span_kind",
        "`skill` or `approval`: the kind of a span whose name varies (`skill.<name>`, `approval.<key>`)",
        "skill.* / approval.*",
    ),
    Attr(
        "openinference.span.kind",
        "span_kind",
        "`AGENT` (root, subagent) · `LLM` (`llm.*`, `api.*`) · `TOOL` (`tool.*`) · `GENERAL` (skill, approval, kanban)",
        "Every span",
    ),
    Attr(
        "traceloop.span.kind",
        "span_kind",
        "The same span kinds in Traceloop's lowercase vocabulary: `agent` · `tool` · `task` · `unknown`",
        "Every span",
    ),
    # ── session ──
    Attr(
        "correlation.id",
        "session",
        "A correlation id the host passed on the hook (`correlation_id` / `correlation.id` / `x-correlation-id` …), reused on every span of that session.",
        "Every span",
    ),
    Attr(
        "gen_ai.conversation.id",
        "session",
        "Same id, gen_ai spelling, wherever `gen_ai.operation.name` is set",
        "Every span",
    ),
    Attr(
        "hermes.cron.job_id",
        "session",
        "Cron job id, `cron` roots only (optional)",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.link",
        "session",
        "On the span **link** from a replacing session's first root to the old session's last root: `previous_session`",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.platform",
        "session",
        "The Hermes surface the turn ran on: `cli` · `telegram` · `discord` · `cron` · … (never reported as a provider)",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.profile",
        "session",
        "The Hermes profile that ran the turn: `default`, the profile id under `~/.hermes/profiles/`, or `custom`.",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.sender.id",
        "session",
        "Gateway sender identity — only with `capture_sender_id: true`.",
        "Every span",
    ),
    Attr("hermes.session.completed", "session", "Hook payload flags", "agent / cron (turn root)"),
    Attr(
        "hermes.session.duration_s",
        "session",
        "On the **log record** `on_session_finalize` / `on_session_reset` emit (exported with `capture_logs`), not on a span",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.session.failed",
        "session",
        "Hermes' `failed` flag on `on_session_end`",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.session.finalize_reason",
        "session",
        "Set when a still-open root was closed by `on_session_finalize` / `on_session_reset`, with `hermes.turn.final_status` `finalized` / `reset` (optional)",
        "agent / cron (turn root)",
    ),
    Attr("hermes.session.interrupted", "session", "Hook payload flags", "agent / cron (turn root)"),
    Attr(
        "hermes.session.is_subagent",
        "session",
        "`true` on a delegated child's own root (optional)",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.session.kind",
        "session",
        "`session` · `cron` (from Hermes' `session_type` / `origin` / `run_type` when a host passes one; Hermes 0.21 passes none, so `cron` comes from a cron…",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.session.previous_id",
        "session",
        "The session this one replaced (`/new`, `/reset`); the root also carries a span link (`hermes.link=previous_session`) to that session's last root (opt…",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.session.reset_reason",
        "session",
        "Set when a still-open root was closed by `on_session_finalize` / `on_session_reset`, with `hermes.turn.final_status` `finalized` / `reset` (optional)",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.session.synthesized",
        "session",
        "`true` on continuation turns: Hermes fires `on_session_start` only for a session's first turn, so later turns open their root lazily at the first hoo…",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.session.turn_count",
        "session",
        "On the **log record** `on_session_finalize` / `on_session_reset` emit (exported with `capture_logs`), not on a span",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.session_id",
        "session",
        "Session id (pre-existing spelling; `session.id` is the standard one)",
        "agent / cron (turn root)",
    ),
    Attr(
        "session.id",
        "session",
        "Hermes session id, on the root and on `llm.*` / `api.*` / `tool.*` / `subagent.*`",
        "Every span",
    ),
    Attr(
        "session_id",
        "session",
        "Hermes session id, the plugin's oldest spelling (`session.id` is the standard one); on the same spans",
        "Every span",
    ),
    Attr(
        "user.id",
        "session",
        "Gateway sender identity — only with `capture_sender_id: true`.",
        "Every span",
    ),
    # ── turn ──
    Attr(
        "hermes.conversation.message_count",
        "turn",
        "Message count when conversation capture is on (optional)",
        "llm.*",
    ),
    Attr(
        "hermes.llm.output.chars",
        "turn",
        "Size of the turn's final response text, observed on `transform_llm_output` ([hooks](/reference/hooks#transform_llm_output)); absent when the finalize…",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.api_call_count",
        "turn",
        "`pre_api_request` hooks fired",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.auxiliary.api_call_count",
        "turn",
        "Auxiliary LLM attempts that ran under this turn (titling, compression, MoA, vision, …) and how many failed (optional)",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.auxiliary.cost.status",
        "turn",
        "Their USD sum, with the same `actual` · `estimated` · `partial` · `included` · `unknown` rule as `hermes.cost.status` (optional)",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.auxiliary.cost.usage",
        "turn",
        "Their USD sum, with the same `actual` · `estimated` · `partial` · `included` · `unknown` rule as `hermes.cost.status` (optional)",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.auxiliary.error_count",
        "turn",
        "Auxiliary LLM attempts that ran under this turn (titling, compression, MoA, vision, …) and how many failed (optional)",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.auxiliary.input_tokens",
        "turn",
        "Their token sum, reported **beside** `gen_ai.usage.*`, never inside it: the `moa` provider's main-loop `api.*` span already carries what the MoA faca…",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.auxiliary.output_tokens",
        "turn",
        "Their token sum, reported **beside** `gen_ai.usage.*`, never inside it: the `moa` provider's main-loop `api.*` span already carries what the MoA faca…",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.duration_s",
        "turn",
        "Turn wall-clock in seconds on the `hermes.turn.end` event",
        "event",
    ),
    Attr(
        "hermes.turn.exit_reason",
        "turn",
        "Hermes' `turn_exit_reason` (`interrupted_by_user`, `guardrail_halt`, `context_compression_timeout`, `max_iterations_reached(n/m)` …), when reported",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.final_status",
        "turn",
        "`completed` · `interrupted` · `failed` · `incomplete` · `timed_out` — from Hermes' `completed` / `interrupted` / `failed` flags (`timed_out` from the…",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.number",
        "turn",
        "1-based index of the user prompt within the session (per process; `hermes -r` restarts at 1).",
        "Every span",
    ),
    Attr(
        "hermes.turn.skill_count",
        "turn",
        "Skills that loaded successfully this turn",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.skills",
        "turn",
        "Skills that loaded successfully this turn",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.tool_commands",
        "turn",
        "`|`-joined distinct paths/URLs and shell commands",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.tool_count",
        "turn",
        "Distinct tool names (sorted CSV, ≤500 chars)",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.tool_outcomes",
        "turn",
        "Sorted CSV of distinct outcomes",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.tool_targets",
        "turn",
        "`|`-joined distinct paths/URLs and shell commands",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.turn.tools",
        "turn",
        "Distinct tool names (sorted CSV, ≤500 chars)",
        "agent / cron (turn root)",
    ),
    # ── usage ──
    Attr(
        "gen_ai.usage.cache_creation.input_tokens",
        "usage",
        "Cache buckets, current spelling (optional)",
        "api.*",
    ),
    Attr(
        "gen_ai.usage.cache_creation_input_tokens",
        "usage",
        "Same values, pre-existing alias kept for older dashboards (optional)",
        "api.*",
    ),
    Attr(
        "gen_ai.usage.cache_read.input_tokens",
        "usage",
        "Cache buckets, current spelling (optional)",
        "api.*",
    ),
    Attr(
        "gen_ai.usage.cache_read_input_tokens",
        "usage",
        "Same values, pre-existing alias kept for older dashboards (optional)",
        "api.*",
    ),
    Attr("gen_ai.usage.input_tokens", "usage", "Same three totals", "api.*"),
    Attr("gen_ai.usage.output_tokens", "usage", "Same three totals", "api.*"),
    Attr("gen_ai.usage.reasoning.output_tokens", "usage", "Reasoning tokens (optional)", "api.*"),
    Attr("gen_ai.usage.total_tokens", "usage", "Same three totals", "api.*"),
    Attr(
        "llm.token_count.completion",
        "usage",
        "Whole prompt (incl. cache reads/writes), completion, sum",
        "api.*",
    ),
    Attr(
        "llm.token_count.completion_details.reasoning",
        "usage",
        "Reasoning tokens, a subset of completion (optional)",
        "api.*",
    ),
    Attr(
        "llm.token_count.prompt",
        "usage",
        "Whole prompt (incl. cache reads/writes), completion, sum",
        "api.*",
    ),
    Attr("llm.token_count.prompt_details.cache_read", "usage", "Cache buckets (optional)", "api.*"),
    Attr(
        "llm.token_count.prompt_details.cache_write", "usage", "Cache buckets (optional)", "api.*"
    ),
    Attr(
        "llm.token_count.total",
        "usage",
        "Whole prompt (incl. cache reads/writes), completion, sum",
        "api.*",
    ),
    # ── cost ──
    Attr(
        "hermes.cost.source",
        "cost",
        "Where the price came from, as Hermes reports it (`official_docs_snapshot`, `provider_models_api`, `none`, …) (optional)",
        "api.*",
    ),
    Attr(
        "hermes.cost.status",
        "cost",
        "`actual` (every call provider-reported) · `estimated` · `partial` (some calls priced, some not: no total) · `included` (subscription route) · `unknow…",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.cost.usage",
        "cost",
        "The turn's USD cost: the sum of its API calls' `hermes.cost.usage`, set **only when every call was priced** (optional)",
        "agent / cron (turn root)",
    ),
    # ── content ──
    Attr("gen_ai.input.messages", "content", "Same content in the gen_ai message shape", "llm.*"),
    Attr("gen_ai.output.messages", "content", "Same in the gen_ai shape", "llm.*"),
    Attr(
        "gen_ai.system_instructions",
        "content",
        "**Full** system prompt: the leading system message, or the Responses API `instructions` — `content_capture: full`",
        "api.*",
    ),
    Attr(
        "hermes.content.input_chars",
        "content",
        "Size of the captured request JSON — `content_capture: full`",
        "api.*",
    ),
    Attr(
        "hermes.content.output_chars",
        "content",
        "Size of the captured response — `content_capture: full`",
        "api.*",
    ),
    Attr(
        "hermes.preview.input.original_chars",
        "content",
        "Set only when the preview was clipped: `true` and the value's original length (optional)",
        "llm.*",
    ),
    Attr(
        "hermes.preview.input.truncated",
        "content",
        "Set only when the preview was clipped: `true` and the value's original length (optional)",
        "llm.*",
    ),
    Attr(
        "hermes.preview.output.original_chars",
        "content",
        "Set only when the preview was clipped: `true` and the value's original length (optional)",
        "llm.*",
    ),
    Attr(
        "hermes.preview.output.truncated",
        "content",
        "Set only when the preview was clipped: `true` and the value's original length (optional)",
        "llm.*",
    ),
    Attr(
        "input.mime_type",
        "content",
        "User message (`text/plain`) or, with `capture_conversation_history`, the conversation JSON (`application/json`)",
        "llm.*",
    ),
    Attr(
        "input.value",
        "content",
        "First user message / final assistant response of the turn (previews; optional)",
        "agent / cron (turn root)",
    ),
    Attr(
        "llm.input_messages",
        "content",
        "OpenInference message list some backends derive from `gen_ai.input.messages`",
        "backend",
    ),
    Attr(
        "llm.output.content",
        "content",
        "OpenInference response text some backends derive from `output.value`",
        "backend",
    ),
    Attr(
        "llm.output.tool_calls",
        "content",
        "OpenInference tool-call list some backends derive from `gen_ai.output.messages`",
        "backend",
    ),
    Attr(
        "llm.system_prompt",
        "content",
        "OpenInference system prompt some backends derive from `gen_ai.system_instructions`",
        "backend",
    ),
    Attr("output.mime_type", "content", "Final assistant response", "llm.*"),
    Attr(
        "output.value",
        "content",
        "First user message / final assistant response of the turn (previews; optional)",
        "agent / cron (turn root)",
    ),
    # ── tool ──
    Attr(
        "gen_ai.tool.call.arguments",
        "tool",
        "Tool args (JSON preview; **full** for `mcp_*` tools in `content_capture: full`)",
        "tool.*",
    ),
    Attr(
        "gen_ai.tool.call.id",
        "tool",
        "Hermes `tool_call_id` (falls back to `task_id` on older Hermes)",
        "tool.*",
    ),
    Attr("gen_ai.tool.call.result", "tool", "Tool result preview", "tool.*"),
    Attr("gen_ai.tool.name", "tool", "Tool name", "tool.*"),
    Attr(
        "gen_ai.tool.type",
        "tool",
        "Tool kind (`function` / `extension`) when a backend reports it",
        "backend",
    ),
    Attr(
        "hermes.terminal.env_type",
        "tool",
        "Terminal tool only: the output size as the terminal tool handed it to Hermes' transform stage (after the capture window, before the head+tail limit,…",
        "tool.*",
    ),
    Attr(
        "hermes.terminal.output.chars",
        "tool",
        "Terminal tool only: the output size as the terminal tool handed it to Hermes' transform stage (after the capture window, before the head+tail limit,…",
        "tool.*",
    ),
    Attr(
        "hermes.terminal.output.dropped_chars",
        "tool",
        "Terminal tool only: whether Hermes' output limit cut the output before the model saw it, and by how many characters (`dropped_chars` only when `true`…",
        "tool.*",
    ),
    Attr(
        "hermes.terminal.output.truncated",
        "tool",
        "Terminal tool only: whether Hermes' output limit cut the output before the model saw it, and by how many characters (`dropped_chars` only when `true`…",
        "tool.*",
    ),
    Attr(
        "hermes.terminal.returncode",
        "tool",
        "Terminal tool only: the output size as the terminal tool handed it to Hermes' transform stage (after the capture window, before the head+tail limit,…",
        "tool.*",
    ),
    Attr(
        "hermes.tool.blocked_by",
        "tool",
        "Which governance floor blocked the call — `deny_rule` · `hardline` · `stdin_password_guard` (optional; only when positively classified, `outcome=bloc…",
        "tool.*",
    ),
    Attr(
        "hermes.tool.command", "tool", "First non-empty `command` / `cmd` arg (optional)", "tool.*"
    ),
    Attr(
        "hermes.tool.cpu.utilization.avg",
        "tool",
        "Process-tree CPU (0..1) during the call — `host_metrics` only",
        "tool.*",
    ),
    Attr(
        "hermes.tool.cpu.utilization.peak",
        "tool",
        "Process-tree CPU (0..1) during the call — `host_metrics` only",
        "tool.*",
    ),
    Attr(
        "hermes.tool.decided_by",
        "tool",
        "`hard_floor` on the tool span when a floor (not a human) blocked the call; see also `hermes.approval.decided_by` on `approval.*` spans (optional)",
        "tool.*",
    ),
    Attr(
        "hermes.tool.duration_s",
        "tool",
        "Tool wall-clock in seconds on the `hermes.tool.call` event (spans carry ms)",
        "event",
    ),
    Attr(
        "hermes.tool.gpu.utilization.avg",
        "tool",
        "Host GPU busy ratio (0..1) during the call — `host_metrics` + GPU only",
        "tool.*",
    ),
    Attr(
        "hermes.tool.gpu.utilization.peak",
        "tool",
        "Host GPU busy ratio (0..1) during the call — `host_metrics` + GPU only",
        "tool.*",
    ),
    Attr(
        "hermes.tool.name", "tool", "Tool name as the `hermes.tool.call` event carries it", "event"
    ),
    Attr(
        "hermes.tool.outcome",
        "tool",
        "`error` · `timeout` · `blocked` · `cancelled` from Hermes' hook status or the tool's own result status; `completed` means the tool returned and nothi…",
        "tool.*",
    ),
    Attr(
        "hermes.tool.result.chars",
        "tool",
        "Size of the result string the model receives, every tool",
        "tool.*",
    ),
    Attr(
        "hermes.tool.target",
        "tool",
        "First non-empty `path` / `file_path` / `target` / `url` / `uri` arg (optional)",
        "tool.*",
    ),
    Attr("tool.name", "tool", "Tool name", "tool.*"),
    # ── skill ──
    Attr("gen_ai.skill.name", "skill", "Bare skill name as Hermes names it", "skill.*"),
    Attr(
        "hermes.skill.name",
        "skill",
        "Bare skill name and `skill_view` / `path_match` when the call loaded a skill (optional)",
        "tool.*",
    ),
    Attr(
        "hermes.skill.path",
        "skill",
        "Directory holding the skill's `SKILL.md`, as reported by `skill_view` (`skill_dir`) or resolved from the file the tool read; omitted when unknown, ne…",
        "skill.*",
    ),
    Attr(
        "hermes.skill.result_status",
        "skill",
        "Turn outcome at close (`completed` · `interrupted` · …)",
        "skill.*",
    ),
    Attr(
        "hermes.skill.source",
        "skill",
        "Bare skill name and `skill_view` / `path_match` when the call loaded a skill (optional)",
        "tool.*",
    ),
    # ── approval ──
    Attr(
        "hermes.approval.choice",
        "approval",
        "`once` · `session` · `always` · `deny` · `timeout` · `smart_approve` · `smart_deny` · `notify_failed`",
        "approval.*",
    ),
    Attr(
        "hermes.approval.command",
        "approval",
        "Gated command and Hermes' description (previews; optional)",
        "approval.*",
    ),
    Attr(
        "hermes.approval.decided_by",
        "approval",
        "`aux_llm` for smart-guardian verdicts, empty for a human answer (optional)",
        "approval.*",
    ),
    Attr(
        "hermes.approval.decision",
        "approval",
        "Event name of an approval decision, as some backends store it as an attribute",
        "event",
    ),
    Attr(
        "hermes.approval.description",
        "approval",
        "Gated command and Hermes' description (previews; optional)",
        "approval.*",
    ),
    Attr("hermes.approval.duration_ms", "approval", "Decision wait time", "approval.*"),
    Attr("hermes.approval.granted", "approval", "Outcome flags", "approval.*"),
    Attr(
        "hermes.approval.pattern_key",
        "approval",
        "Hermes' approval pattern key, when reported (the span is then `approval.<key>`, else `approval`)",
        "approval.*",
    ),
    Attr(
        "hermes.approval.pattern_keys",
        "approval",
        "Hermes' approval pattern key, when reported (the span is then `approval.<key>`, else `approval`)",
        "approval.*",
    ),
    Attr("hermes.approval.surface", "approval", "`cli` · `telegram` · … (optional)", "approval.*"),
    Attr("hermes.approval.timed_out", "approval", "Outcome flags", "approval.*"),
    # ── subagent ──
    Attr("hermes.subagent.child_id", "subagent", "Child session / sub-agent ids", "subagent.*"),
    Attr(
        "hermes.subagent.child_session_id",
        "subagent",
        "Child session / sub-agent ids",
        "subagent.*",
    ),
    Attr(
        "hermes.subagent.duration_ms",
        "subagent",
        "On stop: reported `child_status`, wall-clock, result summary",
        "subagent.*",
    ),
    Attr(
        "hermes.subagent.goal",
        "subagent",
        "Child role (only when Hermes reports one; the span is then `subagent.<role>`, else `subagent`) and delegated goal (preview)",
        "subagent.*",
    ),
    Attr("hermes.subagent.parent_id", "subagent", "Parent identity", "subagent.*"),
    Attr(
        "hermes.subagent.parent_session_id",
        "subagent",
        "On a delegated child's root (optional)",
        "agent / cron (turn root)",
    ),
    Attr("hermes.subagent.parent_turn_id", "subagent", "Parent identity", "subagent.*"),
    Attr(
        "hermes.subagent.role",
        "subagent",
        "On a delegated child's root (optional)",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.subagent.status",
        "subagent",
        "On stop: reported `child_status`, wall-clock, result summary",
        "subagent.*",
    ),
    Attr(
        "hermes.subagent.model_inherited",
        "subagent",
        "Set when the child's root starts: whether the child runs on its parent's model (`false` with `delegation.model` set)",
        "subagent.*",
    ),
    Attr(
        "hermes.subagent.usage.input_tokens",
        "subagent",
        "On stop: tokens of the child's whole subtree (its turns plus its own delegations); namespaced so the child root's `gen_ai.usage.*` is not counted twice",
        "subagent.*",
    ),
    Attr(
        "hermes.subagent.usage.output_tokens",
        "subagent",
        "On stop: tokens of the child's whole subtree (its turns plus its own delegations); namespaced so the child root's `gen_ai.usage.*` is not counted twice",
        "subagent.*",
    ),
    Attr(
        "hermes.subagent.usage.total_tokens",
        "subagent",
        "On stop: tokens of the child's whole subtree (its turns plus its own delegations); namespaced so the child root's `gen_ai.usage.*` is not counted twice",
        "subagent.*",
    ),
    Attr(
        "hermes.subagent.api_call_count",
        "subagent",
        "On stop: API calls in the child's subtree",
        "subagent.*",
    ),
    Attr(
        "hermes.subagent.cost.usage",
        "subagent",
        "On stop: USD of the child's subtree, only when every call was priced (else only the status)",
        "subagent.*",
    ),
    Attr(
        "hermes.subagent.cost.status",
        "subagent",
        "On stop: `actual` / `estimated` / `partial` / `included` / `unknown`, as on the turn root",
        "subagent.*",
    ),
    Attr(
        "hermes.subagent.swept",
        "subagent",
        "`true` when the delegation was closed by its TTL (`subagent_span_ttl_ms`) because `subagent_stop` never arrived",
        "subagent.*",
    ),
    Attr(
        "hermes.subagent.summary",
        "subagent",
        "On stop: reported `child_status`, wall-clock, result summary",
        "subagent.*",
    ),
    # ── kanban ──
    Attr(
        "hermes.kanban.assignee",
        "kanban",
        "The card, its board, the profile assigned to it and the attempt (`run_id` as a string); the join across the dispatcher's and the worker's traces",
        "kanban.*",
    ),
    Attr(
        "hermes.kanban.board",
        "kanban",
        "The kanban card, its board and the attempt a dispatcher-spawned worker is running, from `HERMES_KANBAN_TASK` / `_BOARD` / `_RUN_ID`.",
        "Resource (on every span)",
    ),
    Attr(
        "hermes.kanban.event",
        "kanban",
        "`claimed` · `spawned` · `worker_exited` · `stale_claim` · `completed` · `blocked`",
        "kanban.*",
    ),
    Attr(
        "hermes.kanban.exit_code",
        "kanban",
        "How the worker process ended (`clean_exit` · `rate_limited` · `nonzero_exit` · `signaled` · `unknown`), its exit code, and what the dispatcher does n…",
        "kanban.*",
    ),
    Attr(
        "hermes.kanban.exit_kind",
        "kanban",
        "How the worker process ended (`clean_exit` · `rate_limited` · `nonzero_exit` · `signaled` · `unknown`), its exit code, and what the dispatcher does n…",
        "kanban.*",
    ),
    Attr(
        "hermes.kanban.heartbeat_stale",
        "kanban",
        "Whether the reclaimed claim had stopped heart-beating (`kanban.stale_claim`)",
        "kanban.*",
    ),
    Attr(
        "hermes.kanban.outcome",
        "kanban",
        "`completed` · `blocked` on the worker-side spans and on the worker's `agent` root; the dispatcher's own outcome word on `kanban.worker_exit` (`crashe…",
        "kanban.*",
    ),
    Attr(
        "hermes.kanban.reason",
        "kanban",
        "The worker's hand-off summary (`kanban.complete`) or block reason (`kanban.block`), preview-clipped; absent when `capture_previews=false`",
        "kanban.*",
    ),
    Attr(
        "hermes.kanban.retry_status",
        "kanban",
        "How the worker process ended (`clean_exit` · `rate_limited` · `nonzero_exit` · `signaled` · `unknown`), its exit code, and what the dispatcher does n…",
        "kanban.*",
    ),
    Attr(
        "hermes.kanban.run_id",
        "kanban",
        "The kanban card, its board and the attempt a dispatcher-spawned worker is running, from `HERMES_KANBAN_TASK` / `_BOARD` / `_RUN_ID`.",
        "Resource (on every span)",
    ),
    Attr(
        "hermes.kanban.summary",
        "kanban",
        "The worker's hand-off summary (`kanban.complete`) or block reason (`kanban.block`), preview-clipped; absent when `capture_previews=false`",
        "kanban.*",
    ),
    Attr(
        "hermes.kanban.task_id",
        "kanban",
        "The kanban card, its board and the attempt a dispatcher-spawned worker is running, from `HERMES_KANBAN_TASK` / `_BOARD` / `_RUN_ID`.",
        "Resource (on every span)",
    ),
    Attr(
        "hermes.kanban.worker_pid",
        "kanban",
        "The worker process (`kanban.spawn`, `kanban.worker_exit`, `kanban.stale_claim`)",
        "kanban.*",
    ),
    # ── error ──
    Attr(
        "error.message",
        "error",
        "Result `error` text when the outcome is `error` (optional)",
        "tool.*",
    ),
    Attr(
        "error.type",
        "error",
        "Most recent provider error class this turn (optional)",
        "agent / cron (turn root)",
    ),
    Attr(
        "exception.escaped",
        "error",
        "`true` on the `exception` span event a failed API request records",
        "api.* on failure",
    ),
    Attr(
        "exception.message",
        "error",
        "The error text on the `exception` span event / a forwarded log record",
        "api.* on failure",
    ),
    Attr(
        "exception.stacktrace",
        "error",
        "Stack trace of a forwarded log record with `exc_info`",
        "log record",
    ),
    Attr(
        "exception.type",
        "error",
        "Error class on the `exception` span event / a forwarded log record",
        "api.* on failure",
    ),
    Attr(
        "gen_ai.response.status_code",
        "error",
        "HTTP status (omitted for network errors)",
        "api.* on failure (api_request_error)",
    ),
    Attr(
        "hermes.api.error",
        "error",
        "Event name of a failed API request, as some backends store it as an attribute",
        "event",
    ),
    Attr(
        "hermes.max_retries",
        "error",
        "Retry state for this request",
        "api.* on failure (api_request_error)",
    ),
    Attr(
        "hermes.retry.count",
        "error",
        "Retry state for this request",
        "api.* on failure (api_request_error)",
    ),
    Attr(
        "hermes.retryable",
        "error",
        "Retry state for this request",
        "api.* on failure (api_request_error)",
    ),
    Attr(
        "http.response.status_code",
        "error",
        "HTTP status (omitted for network errors)",
        "api.* on failure (api_request_error)",
    ),
    # ── log ──
    Attr(
        "code.file.path",
        "log",
        "Source location of a forwarded log record (OTel semconv)",
        "log record",
    ),
    Attr(
        "code.filepath",
        "log",
        "Source location of a forwarded log record (pre-1.33 spelling)",
        "log record",
    ),
    Attr(
        "code.function",
        "log",
        "Function of a forwarded log record (pre-1.33 spelling)",
        "log record",
    ),
    Attr(
        "code.function.name",
        "log",
        "Function of a forwarded log record (OTel semconv)",
        "log record",
    ),
    Attr("code.line.number", "log", "Line of a forwarded log record (OTel semconv)", "log record"),
    Attr("code.lineno", "log", "Line of a forwarded log record (pre-1.33 spelling)", "log record"),
    Attr(
        "hermes.log.attribution",
        "log",
        "How a forwarded log record was tied to a turn: `context` (an open span) or `session_tag`",
        "log record",
    ),
    # ── model ──
    Attr(
        "gen_ai.agent.name",
        "model",
        "`hermes-agent` (a constant unless the host passes `agent_name`; Weave shows it as the agent)",
        "agent / cron (turn root)",
    ),
    Attr("gen_ai.operation.name", "model", "`invoke_agent`", "agent / cron (turn root)"),
    Attr(
        "gen_ai.provider.name",
        "model",
        "The LLM provider the turn's API calls reported (`openrouter`, `anthropic` …), set at **end**; absent when no API call reported one.",
        "agent / cron (turn root)",
    ),
    Attr(
        "gen_ai.request.choice.count",
        "model",
        "Request parameters, each only when Hermes passes it (optional)",
        "api.*",
    ),
    Attr(
        "gen_ai.request.frequency_penalty",
        "model",
        "Request parameters, each only when Hermes passes it (optional)",
        "api.*",
    ),
    Attr(
        "gen_ai.request.max_tokens",
        "model",
        "Request parameters, each only when Hermes passes it (optional)",
        "api.*",
    ),
    Attr("gen_ai.request.model", "model", "Model name", "agent / cron (turn root)"),
    Attr(
        "hermes.turn.trigger",
        "session",
        "What started the turn: `user`, `cron`, `kanban`, `subagent`, `a2a`, `api`, `oneshot`, `curator`, `batch` or `system`",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.agent.model_override",
        "model",
        "Whether the turn runs on a model other than the profile's `model.default` (delegation.model, kanban `--model`, a cron job's model, `-m`, `/model`); absent when either is unknown",
        "agent / cron (turn root)",
    ),
    Attr(
        "gen_ai.request.presence_penalty",
        "model",
        "Request parameters, each only when Hermes passes it (optional)",
        "api.*",
    ),
    Attr(
        "gen_ai.request.reasoning.level",
        "model",
        "Request parameters, each only when Hermes passes it (optional)",
        "api.*",
    ),
    Attr(
        "gen_ai.request.stop_sequences",
        "model",
        "Request parameters, each only when Hermes passes it (optional)",
        "api.*",
    ),
    Attr(
        "gen_ai.request.stream",
        "model",
        "Request parameters, each only when Hermes passes it (optional)",
        "api.*",
    ),
    Attr(
        "gen_ai.request.temperature",
        "model",
        "Request parameters, each only when Hermes passes it (optional)",
        "api.*",
    ),
    Attr(
        "gen_ai.request.top_k",
        "model",
        "Request parameters, each only when Hermes passes it (optional)",
        "api.*",
    ),
    Attr(
        "gen_ai.request.top_p",
        "model",
        "Request parameters, each only when Hermes passes it (optional)",
        "api.*",
    ),
    Attr("gen_ai.response.finish_reasons", "model", "`['stop']`, `['tool_use']`, …", "api.*"),
    Attr(
        "gen_ai.response.id",
        "model",
        "Response model / id, only when the provider reported them (optional)",
        "api.*",
    ),
    Attr("gen_ai.response.model", "model", "Model at turn end", "agent / cron (turn root)"),
    Attr(
        "gen_ai.system",
        "model",
        "The LLM provider the turn's API calls reported (`openrouter`, `anthropic` …), set at **end**; absent when no API call reported one.",
        "agent / cron (turn root)",
    ),
    Attr(
        "hermes.auxiliary.task",
        "model",
        "The auxiliary task, as Hermes names it (`title_generation`, `compression`, `moa_reference`, `moa_aggregator`, `vision`, `approval`, `kanban_decompose…",
        "api.*",
    ),
    Attr(
        "hermes.query_source",
        "model",
        "Which loop the call belongs to: `main` (the agent loop), `subagent` (a delegated child's own loop), `auxiliary` (a call Hermes made on the agent's be…",
        "api.*",
    ),
    Attr(
        "llm.api_mode",
        "model",
        "`chat_completions` · `anthropic_messages` · `codex_responses` · …",
        "api.*",
    ),
    Attr("llm.model_name", "model", "Model the turn started with", "agent / cron (turn root)"),
    Attr(
        "llm.provider",
        "model",
        "The LLM provider the turn's API calls reported (`openrouter`, `anthropic` …), set at **end**; absent when no API call reported one.",
        "agent / cron (turn root)",
    ),
    Attr("llm.request.approx_input_tokens", "model", "Request shape as Hermes reports it", "api.*"),
    Attr("llm.request.max_tokens", "model", "Request shape as Hermes reports it", "api.*"),
    Attr("llm.request.message_count", "model", "Request shape as Hermes reports it", "api.*"),
    Attr(
        "llm.request.tool_count",
        "model",
        "Tools offered on an auxiliary call, when any (optional; auxiliary spans only)",
        "api.*",
    ),
    Attr("llm.response.duration_ms", "model", "Wall-clock of the request", "api.*"),
    Attr("llm.response.finish_reason", "model", "Same, scalar", "api.*"),
    Attr(
        "llm.response.output_chars",
        "model",
        "Assistant content length / tool-call count (optional)",
        "api.*",
    ),
    Attr(
        "llm.response.tool_calls",
        "model",
        "Assistant content length / tool-call count (optional)",
        "api.*",
    ),
    Attr(
        "wandb.is_turn",
        "model",
        "Marks a Weave conversation turn and groups turns by session",
        "agent / cron (turn root)",
    ),
    Attr(
        "wandb.thread_id",
        "model",
        "Marks a Weave conversation turn and groups turns by session",
        "agent / cron (turn root)",
    ),
    Attr("weave.agent.version", "model", "Plugin version (optional)", "agent / cron (turn root)"),
)

ATTRIBUTE_NAMES: Tuple[str, ...] = tuple(a.name for a in ATTRIBUTES)
_BY_NAME: Dict[str, Attr] = {a.name: a for a in ATTRIBUTES}


def attribute(name: str) -> Optional[Attr]:
    """The registry entry for ``name``, or ``None``."""
    return _BY_NAME.get(name)


def groups() -> Dict[str, List[Attr]]:
    """``{group: [Attr, ...]}`` in registry order."""
    out: Dict[str, List[Attr]] = {}
    for a in ATTRIBUTES:
        out.setdefault(a.group, []).append(a)
    return out


# ── Fallback chains: where a reader finds a value, first match wins ──────
#
# Both conventions are emitted side by side; a backend may keep one, both,
# or flatten the names. ``first(attrs, KEYS)`` is the one lookup rule.

SESSION_KEYS: Tuple[str, ...] = ("hermes.session_id", "session.id", "session_id")
# Backend search cards may carry a vendor's own session key too.
CARD_SESSION_KEYS: Tuple[str, ...] = (
    "hermes.session_id",
    "langfuse.sessionId",
    "session.id",
    "session_id",
)
MODEL_KEYS: Tuple[str, ...] = ("gen_ai.request.model", "llm.model_name", "gen_ai.response.model")
PROVIDER_KEYS: Tuple[str, ...] = ("gen_ai.provider.name", "llm.provider", "gen_ai.system")
INPUT_TOKEN_KEYS: Tuple[str, ...] = ("gen_ai.usage.input_tokens", "llm.token_count.prompt")
OUTPUT_TOKEN_KEYS: Tuple[str, ...] = ("gen_ai.usage.output_tokens", "llm.token_count.completion")
TOTAL_TOKEN_KEYS: Tuple[str, ...] = ("gen_ai.usage.total_tokens", "llm.token_count.total")
REASONING_TOKEN_KEYS: Tuple[str, ...] = (
    "gen_ai.usage.reasoning.output_tokens",
    "llm.token_count.completion_details.reasoning",
)
CACHE_READ_TOKEN_KEYS: Tuple[str, ...] = (
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_read_input_tokens",
    "llm.token_count.prompt_details.cache_read",
)
FINISH_REASON_KEYS: Tuple[str, ...] = (
    "gen_ai.response.finish_reasons",
    "llm.response.finish_reason",
)
INPUT_CONTENT_KEYS: Tuple[str, ...] = (
    "input.value",
    "gen_ai.input.messages",
    "gen_ai.tool.call.arguments",
)
OUTPUT_CONTENT_KEYS: Tuple[str, ...] = (
    "output.value",
    "gen_ai.output.messages",
    "gen_ai.tool.call.result",
)
COST_KEYS: Tuple[str, ...] = ("hermes.cost.usage",)
COST_KEY = COST_KEYS[0]
COST_STATUS_KEY = "hermes.cost.status"
# The turn's totals live on the root; a matched api span must not pass for them.
TURN_TOTAL_KEYS: Tuple[str, ...] = (
    "gen_ai.usage.total_tokens",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "llm.token_count.total",
    "llm.token_count.prompt",
    "llm.token_count.completion",
    "hermes.cost.usage",
)
TURN_NUMBER_KEY = "hermes.turn.number"
PLATFORM_KEY = "hermes.platform"
QUERY_SOURCE_KEY = "hermes.query_source"
QUERY_SOURCE_AUXILIARY = "auxiliary"
ERROR_TYPE_KEY = "error.type"
TOOL_NAME_KEY = "tool.name"


def first(attrs: Mapping[str, Any], keys: Sequence[str]) -> Any:
    """The first non-empty value of ``keys`` in ``attrs`` (``None`` / ``""`` skip)."""
    for k in keys:
        v = attrs.get(k)
        if v is not None and v != "":
            return v
    return None


# ── Span kinds ────────────────────────────────────────────────────────────
#
# The dashboard's icon and colour per span, the live store's ``kind=`` filter
# and the query CLI agree on this table. A kind is recognised by the span
# name's prefix (``exact`` names match whole), except where the span carries
# ``hermes.span_kind`` (skill and approval spans, whose names vary). Order
# matters: the first rule that matches wins, and ``other`` is none of them.

KIND_OVERRIDE_ATTR = "hermes.span_kind"
KIND_OVERRIDES: Tuple[str, ...] = ("skill", "approval")


@dataclass(frozen=True)
class KindRule:
    kind: str
    prefixes: Tuple[str, ...] = ()
    exact: Tuple[str, ...] = ()


SPAN_KINDS: Tuple[KindRule, ...] = (
    KindRule("agent", prefixes=("agent.",), exact=("agent",)),
    KindRule("cron", prefixes=("cron",)),
    KindRule("session", prefixes=("session",)),
    KindRule("skill", prefixes=("skill.",)),
    KindRule("approval", prefixes=("approval",)),
    KindRule("subagent", prefixes=("subagent",)),
    KindRule("llm", prefixes=("llm.",)),
    KindRule("api", prefixes=("api.",)),
    KindRule("tool", prefixes=("tool.",)),
)
KINDS: Tuple[str, ...] = tuple(r.kind for r in SPAN_KINDS) + ("other",)
# Name prefixes per kind, as ``LIKE`` patterns need them (an exact name is its own prefix).
KIND_PREFIXES: Dict[str, Tuple[str, ...]] = {
    r.kind: tuple(dict.fromkeys(r.exact + r.prefixes)) for r in SPAN_KINDS
}


def span_kind(name: str, attrs: Optional[Mapping[str, Any]] = None) -> str:
    """The kind of a span from its name and attributes (``kindOf`` in ``lib.ts``)."""
    a = attrs or {}
    override = a.get(KIND_OVERRIDE_ATTR)
    if override in KIND_OVERRIDES:
        return str(override)
    n = (name or "").lower()
    for rule in SPAN_KINDS:
        if n in rule.exact or any(n.startswith(p) for p in rule.prefixes):
            return rule.kind
    return "other"


# Names the generated TypeScript test replays through ``kindOf`` so the two
# classifiers cannot drift (``scripts/gen_semconv.py`` records the answers).
KIND_EXAMPLES: Tuple[Tuple[str, Dict[str, str]], ...] = (
    ("agent", {}),
    ("agent.main", {}),
    ("agentic-thing", {}),
    ("cron", {}),
    ("cron.nightly", {}),
    ("session", {}),
    ("session:abc", {}),
    ("skill.observability", {}),
    ("tool.skill_view", {KIND_OVERRIDE_ATTR: "skill"}),
    ("approval", {}),
    ("approval.rm_rf", {}),
    ("tool.terminal", {KIND_OVERRIDE_ATTR: "approval"}),
    ("subagent", {}),
    ("subagent.researcher", {}),
    ("llm.gpt-4", {}),
    ("api.gpt-4", {}),
    ("api.error", {}),
    ("tool.terminal", {}),
    ("tool.read_file", {KIND_OVERRIDE_ATTR: "nonsense"}),
    ("kanban.claim", {}),
    ("MCP send ping", {}),
    ("", {}),
)


# ── Events (log records with an ``event_name``) ───────────────────────────

EVENT_TURN_START = "hermes.turn.start"
EVENT_TURN_END = "hermes.turn.end"
EVENT_TOOL_CALL = "hermes.tool.call"
EVENT_APPROVAL_DECISION = "hermes.approval.decision"
EVENT_API_ERROR = "hermes.api.error"
EVENT_SUBAGENT_START = "hermes.subagent.start"
EVENT_SUBAGENT_STOP = "hermes.subagent.stop"
EVENT_SESSION_FINALIZE = "hermes.session.finalize"
EVENT_KANBAN_TRANSITION = "hermes.kanban.transition"
EVENT_AUXILIARY_CALL = "hermes.auxiliary.call"
EVENT_INFERENCE_DETAILS = "gen_ai.client.inference.operation.details"
EVENT_NAMES: Tuple[str, ...] = (
    EVENT_TURN_START,
    EVENT_TURN_END,
    EVENT_TOOL_CALL,
    EVENT_APPROVAL_DECISION,
    EVENT_API_ERROR,
    EVENT_SUBAGENT_START,
    EVENT_SUBAGENT_STOP,
    EVENT_SESSION_FINALIZE,
    EVENT_KANBAN_TRANSITION,
    EVENT_AUXILIARY_CALL,
    EVENT_INFERENCE_DETAILS,
)


def unknown_names(names: Iterable[str]) -> List[str]:
    """Those of ``names`` that are not registered (for the drift tests)."""
    return sorted(n for n in set(names) if n not in _BY_NAME)
