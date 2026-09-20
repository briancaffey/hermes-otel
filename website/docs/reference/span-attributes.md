---
sidebar_position: 3
title: "Span attribute reference"
description: "Every attribute the plugin sets, by span type."
---

# Span attribute reference

Every attribute the plugin may set, grouped by span type. See [Attribute conventions](/architecture/attributes) for the narrative version of the dual-convention mapping (OpenInference `llm.*` / `input.value` for Phoenix **and** OTel GenAI `gen_ai.*` for Langfuse, Weave and generic dashboards).

Attributes marked **optional** are only set when the underlying data is available. Previews (`input.value`, `output.value`, `gen_ai.*.messages`, tool args/results) are gated by `capture_previews` and clipped to `preview_max_chars` (or the per-category cap); the `capture_full_*` flags add the untruncated fields noted below.

A test (`tests/unit/test_span_attributes_docs.py`) fails if the code sets an attribute this page does not list, or this page lists one the code never sets.

## Resource (on every span)

| Attribute | Source |
|---|---|
| `service.name` | `hermes-agent` (fixed; override via `resource_attributes:`) |
| `service.instance.id` | UUID generated once per process — keeps two Hermes processes on different metric series |
| `service.version` | Plugin version, read from the shipped `plugin.yaml` |
| `process.pid` | Process id |
| `openinference.project.name` | `project_name` / `OTEL_PROJECT_NAME` (the Phoenix project); optional |
| `host.name` | Hostname, only when `host_metrics: true` |
| `wandb.entity`, `wandb.project` | W&B Weave routing (when configured) |
| `telemetry.sdk.*` | Set by the OTel SDK |
| *any* `resource_attributes.*` / `global_tags.*` | From the config file; `resource_attributes` wins on key conflict |

## Every span

| Attribute | Convention | Meaning |
|---|---|---|
| `openinference.span.kind` | OpenInference | `AGENT` (root, subagent) · `LLM` (`llm.*`, `api.*`) · `TOOL` (`tool.*`) · `CHAIN` (skill, approval) |
| `hermes.turn.number` | hermes | 1-based index of the user prompt within the session (per process; `hermes -r` restarts at 1). On the root and on every `llm.*` / `api.*` / `tool.*` span of the turn |
| `session.id` | OTel | Hermes session id, on the root and on `llm.*` / `api.*` / `tool.*` / `subagent.*` |
| `gen_ai.conversation.id` | gen_ai | Same id, gen_ai spelling, wherever `gen_ai.operation.name` is set |
| `correlation.id` | hermes | Correlation id Hermes passes on the hook (optional) |
| `user.id`, `hermes.sender.id` | OTel / hermes | Gateway sender identity — only with `capture_sender_id: true`. `user.id` is `platform:sender` |

## `agent` / `cron` (turn root)

The root span is named `agent`, or `cron` when the session kind is a cron job. Set at **start**:

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `hermes.session.kind` | hermes | string | `session` · `cron` (from Hermes' `session_type` / `origin` / `run_type` when a host passes one; Hermes 0.21 passes none, so `cron` comes from a cron platform and everything else is `session`) |
| `hermes.session_id` | hermes | string | Session id (pre-existing spelling; `session.id` is the standard one) |
| `llm.model_name` | OpenInference | string | Model the turn started with |
| `hermes.platform` | hermes | string | The Hermes surface the turn ran on: `cli` · `telegram` · `discord` · `cron` · … (never reported as a provider) |
| `llm.provider`, `gen_ai.provider.name`, `gen_ai.system` | both | string | The LLM provider the turn's API calls reported (`openrouter`, `anthropic` …), set at **end**; absent when no API call reported one. Hermes 0.21 passes no provider on `on_session_start` |
| `gen_ai.request.model` | gen_ai | string | Model name |
| `gen_ai.operation.name` | gen_ai | string | `invoke_agent` |
| `gen_ai.agent.name` | gen_ai | string | `hermes-agent` (a constant unless the host passes `agent_name`; Weave shows it as the agent) |
| `wandb.is_turn`, `wandb.thread_id` | Weave | bool / string | Marks a Weave conversation turn and groups turns by session |
| `weave.agent.version` | Weave | string | Plugin version (optional) |
| `hermes.session.synthesized` | hermes | bool | `true` when the root was created lazily because `on_session_start` never fired (optional) |
| `hermes.cron.job_id` | hermes | string | Cron job id, `cron` roots only (optional) |
| `hermes.session.is_subagent` | hermes | bool | `true` on a delegated child's own root (optional) |
| `hermes.subagent.role`, `hermes.subagent.parent_session_id` | hermes | string | On a delegated child's root (optional) |

Set at **end** (turn summary; empty/zero aggregators are omitted):

| Attribute | Type | Meaning |
|---|---|---|
| `hermes.session.completed`, `hermes.session.interrupted` | bool | Hook payload flags |
| `hermes.turn.final_status` | hermes | string | `completed` · `interrupted` · `failed` · `incomplete` · `timed_out` — from Hermes' `completed` / `interrupted` / `failed` flags (`timed_out` from the orphan sweep) |
| `hermes.session.failed` | hermes | bool | Hermes' `failed` flag on `on_session_end` |
| `hermes.turn.exit_reason` | hermes | string | Hermes' `turn_exit_reason` (`interrupted_by_user`, `guardrail_halt`, `context_compression_timeout`, `max_iterations_reached(n/m)` …), when reported |
| `hermes.turn.tool_count`, `hermes.turn.tools` | int / string | Distinct tool names (sorted CSV, ≤500 chars) |
| `hermes.turn.tool_targets`, `hermes.turn.tool_commands` | string | `\|`-joined distinct paths/URLs and shell commands |
| `hermes.turn.tool_outcomes` | string | Sorted CSV of distinct outcomes |
| `hermes.turn.skill_count`, `hermes.turn.skills` | int / string | Skills that loaded successfully this turn |
| `hermes.turn.api_call_count` | int | `pre_api_request` hooks fired |
| `gen_ai.response.model` | string | Model at turn end |
| `error.type` | string | Most recent provider error class this turn (optional) |
| `input.value`, `output.value` | string | First user message / final assistant response of the turn (previews; optional) |

## `llm.*`

One per `run_conversation` call (span kind `LLM`).

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `llm.model_name` | OpenInference | string | Model |
| `llm.provider`, `gen_ai.provider.name`, `gen_ai.system` | both | string | The LLM provider reported by this turn's API calls; absent on the first call before any API request reported one |
| `gen_ai.request.model` | gen_ai | string | Model name |
| `gen_ai.operation.name` | gen_ai | string | `chat` |
| `input.value`, `input.mime_type` | OpenInference | string | User message (`text/plain`) or, with `capture_conversation_history`, the conversation JSON (`application/json`) |
| `gen_ai.input.messages` | gen_ai | string (JSON) | Same content in the gen_ai message shape |
| `output.value`, `output.mime_type` | OpenInference | string | Final assistant response |
| `gen_ai.output.messages` | gen_ai | string (JSON) | Same in the gen_ai shape |
| `gen_ai.response.model` | gen_ai | string | The response model a provider reported on this turn's API calls; absent when none was reported (never the request model) |
| `hermes.conversation.message_count` | hermes | int | Message count when conversation capture is on (optional) |

## `api.*`

One per HTTP round-trip to the provider (span kind `LLM`). Set at **start**:

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `llm.model_name`, `llm.provider` | OpenInference | string | Model / provider |
| `gen_ai.request.model`, `gen_ai.system`, `gen_ai.provider.name` | gen_ai | string | Model / provider (`gen_ai.system` is the legacy spelling Langfuse reads) |
| `gen_ai.operation.name` | gen_ai | string | `chat` |
| `llm.api_mode` | hermes | string | `chat_completions` · `anthropic_messages` · `codex_responses` · … |
| `llm.request.message_count`, `llm.request.approx_input_tokens`, `llm.request.max_tokens` | hermes | int | Request shape as Hermes reports it |
| `gen_ai.request.max_tokens`, `gen_ai.request.temperature`, `gen_ai.request.top_p`, `gen_ai.request.top_k`, `gen_ai.request.frequency_penalty`, `gen_ai.request.presence_penalty`, `gen_ai.request.stream`, `gen_ai.request.reasoning.level`, `gen_ai.request.stop_sequences`, `gen_ai.request.choice.count` | gen_ai | mixed | Request parameters, each only when Hermes passes it (optional) |
| `input.value`, `input.mime_type` | OpenInference | string | Request preview |
| `llm.input_messages`, `gen_ai.input.messages` | both | string (JSON) | **Full** request messages — `capture_full_prompts: true` only |
| `llm.system_prompt`, `gen_ai.system_instructions` | both | string | **Full** system prompt — `capture_full_prompts: true` only |

Set at **end** (success):

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total` | OpenInference | int | Whole prompt (incl. cache reads/writes), completion, sum |
| `llm.token_count.prompt_details.cache_read`, `llm.token_count.prompt_details.cache_write` | OpenInference | int | Cache buckets (optional) |
| `llm.token_count.completion_details.reasoning` | OpenInference | int | Reasoning tokens, a subset of completion (optional) |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.total_tokens` | gen_ai | int | Same three totals |
| `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens` | gen_ai | int | Cache buckets, current spelling (optional) |
| `gen_ai.usage.cache_read_input_tokens`, `gen_ai.usage.cache_creation_input_tokens` | gen_ai | int | Same values, pre-existing alias kept for older dashboards (optional) |
| `gen_ai.usage.reasoning.output_tokens` | gen_ai | int | Reasoning tokens (optional) |
| `gen_ai.response.model`, `gen_ai.response.id` | gen_ai | string | Response model / id, only when the provider reported them (optional) |
| `gen_ai.response.finish_reasons` | gen_ai | string[] | `["stop"]`, `["tool_use"]`, … |
| `llm.response.finish_reason` | hermes | string | Same, scalar |
| `llm.response.duration_ms` | hermes | float | Wall-clock of the request |
| `llm.response.output_chars`, `llm.response.tool_calls` | hermes | int | Assistant content length / tool-call count (optional) |
| `output.value`, `output.mime_type` | OpenInference | string | Response preview |
| `llm.output.content`, `llm.output.tool_calls`, `gen_ai.output.messages` | both | string (JSON) | **Full** response — `capture_full_responses: true` only |

### `api.*` on failure (`api_request_error`)

When the request fails, the span ends with `StatusCode.ERROR`, an `exception` event (`exception.type` / `exception.message` / `exception.escaped`), and:

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `error.type` | OTel | string | Error class reported by Hermes (e.g. `RateLimitError`) |
| `http.response.status_code`, `gen_ai.response.status_code` | OTel / gen_ai | int | HTTP status (omitted for network errors) |
| `hermes.retry.count`, `hermes.max_retries`, `hermes.retryable` | hermes | int / int / bool | Retry state for this request |
| `llm.response.duration_ms` | hermes | float | Wall-clock of the failed attempt |

The most recent `error.type` is also stamped on the turn's root span at `on_session_end`.

## `tool.*`

One per tool call (span kind `TOOL`), keyed by Hermes' `tool_call_id` so parallel calls never collide.

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `tool.name`, `gen_ai.tool.name` | both | string | Tool name |
| `gen_ai.tool.call.id` | gen_ai | string | Hermes `tool_call_id` (falls back to `task_id` on older Hermes) |
| `gen_ai.operation.name` | gen_ai | string | `execute_tool` |
| `input.value`, `gen_ai.tool.call.arguments` | both | string | Tool args (JSON preview; **full** for `mcp_*` tools with `capture_full_prompts`) |
| `output.value`, `gen_ai.tool.call.result` | both | string | Tool result preview |
| `hermes.tool.target` | hermes | string | First non-empty `path` / `file_path` / `target` / `url` / `uri` arg (optional) |
| `hermes.tool.command` | hermes | string | First non-empty `command` / `cmd` arg (optional) |
| `hermes.tool.outcome` | hermes | string | `error` · `timeout` · `blocked` · `cancelled` from Hermes' hook status or the tool's own result status; `completed` means the tool returned and nothing reported a failure |
| `hermes.tool.blocked_by` | hermes | string | Which governance floor blocked the call — `deny_rule` · `hardline` · `stdin_password_guard` (optional; only when positively classified, `outcome=blocked`) |
| `hermes.tool.decided_by` | hermes | string | `hard_floor` on the tool span when a floor (not a human) blocked the call; see also `hermes.approval.decided_by` on `approval.*` spans (optional) |
| `error.message` | OTel | string | Result `error` text when the outcome is `error` (optional) |
| `hermes.skill.name`, `hermes.skill.source` | hermes | string | Bare skill name and `skill_view` / `path_match` when the call loaded a skill (optional) |
| `hermes.tool.cpu.utilization.avg`, `hermes.tool.cpu.utilization.peak` | hermes | float | Process-tree CPU (0..1) during the call — `host_metrics` only |
| `hermes.tool.gpu.utilization.avg`, `hermes.tool.gpu.utilization.peak` | hermes | float | Host GPU busy ratio (0..1) during the call — `host_metrics` + GPU only |

The utilization attributes are absent when host metrics are off or the tool finished between two samples. See [Host & GPU metrics](/configuration/host-metrics).

## `skill.*`

One per skill that loaded successfully this turn (`skill_spans: true`), nested under the root; opens when the loading tool call ends and closes at turn end.

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `hermes.skill.name`, `gen_ai.skill.name` | both | string | Bare skill name as Hermes names it |
| `hermes.skill.source` | hermes | string | `skill_view` · `path_match` |
| `hermes.skill.path` | hermes | string | Directory holding the skill's `SKILL.md`, as reported by `skill_view` (`skill_dir`) or resolved from the file the tool read; omitted when unknown, never derived from the name (optional) |
| `hermes.span_kind` | hermes | string | `skill` |
| `gen_ai.operation.name` | gen_ai | string | `execute_skill` |
| `hermes.skill.result_status` | hermes | string | Turn outcome at close (`completed` · `interrupted` · …) |

## `approval.*`

One per human-in-the-loop (or smart-guardian) approval prompt, named `approval.<pattern_key>`.

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `hermes.approval.pattern_key`, `hermes.approval.pattern_keys` | hermes | string | Hermes' approval pattern key, when reported (the span is then `approval.<key>`, else `approval`) |
| `hermes.approval.surface` | hermes | string | `cli` · `telegram` · … (optional) |
| `hermes.approval.command`, `hermes.approval.description` | hermes | string | Gated command and Hermes' description (previews; optional) |
| `gen_ai.tool.call.id` | gen_ai | string | Correlates to the gated `tool.*` span (optional) |
| `hermes.span_kind` | hermes | string | `approval` |
| `hermes.approval.granted`, `hermes.approval.timed_out` | hermes | bool | Outcome flags |
| `hermes.approval.choice` | hermes | string | `once` · `session` · `always` · `deny` · `timeout` · `smart_approve` · `smart_deny` · `notify_failed` |
| `hermes.approval.decided_by` | hermes | string | `aux_llm` for smart-guardian verdicts, empty for a human answer (optional) |
| `hermes.approval.duration_ms` | hermes | float | Decision wait time |

## `subagent.*`

One per delegated child agent (span kind `AGENT`); the child's own root nests beneath it (or is linked, cross-process).

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `gen_ai.operation.name`, `gen_ai.agent.name` | gen_ai | string | `invoke_agent` / child role (`gen_ai.agent.name` absent when no role was reported) |
| `hermes.subagent.role`, `hermes.subagent.goal` | hermes | string | Child role (only when Hermes reports one; the span is then `subagent.<role>`, else `subagent`) and delegated goal (preview) |
| `input.value` | OpenInference | string | The goal preview |
| `hermes.subagent.child_session_id`, `hermes.subagent.child_id` | hermes | string | Child session / sub-agent ids |
| `hermes.subagent.parent_session_id`, `hermes.subagent.parent_turn_id`, `hermes.subagent.parent_id` | hermes | string | Parent identity |
| `hermes.subagent.status`, `hermes.subagent.duration_ms`, `hermes.subagent.summary`, `output.value` | hermes | mixed | On stop: reported `child_status`, wall-clock, result summary |

## Metrics

Metrics are documented on their own page: [Metrics reference](/reference/metrics).
