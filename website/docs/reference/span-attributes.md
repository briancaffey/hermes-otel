---
sidebar_position: 3
title: "Span attribute reference"
description: "Every attribute the plugin sets, by span type."
---

# Span attribute reference

Every attribute the plugin may set, grouped by span type. See [Attribute conventions](/architecture/attributes) for the narrative version of the dual-convention mapping.

Attributes marked **optional** are only set when the underlying data is available.

## Resource (on every span)

| Attribute | Source |
|---|---|
| `service.name` | `OTEL_PROJECT_NAME` / `project_name` (fallback: `hermes-agent`) |
| `service.version` | `hermes-otel` plugin version |
| `otel.scope.name` | `hermes-otel` |
| `openinference.project.name` | Same as `service.name` |
| `telemetry.sdk.*` | Set by OTel SDK |
| *any* `resource_attributes.*` | From `config.yaml` |
| *any* `global_tags.*` | From `config.yaml` (overridden by `resource_attributes` on key conflict) |

## `session.*` / `cron`

Set at **start**:

| Attribute | Type | Meaning |
|---|---|---|
| `hermes.session.kind` | string | `cli` · `telegram` · `discord` · `cron` · ... |
| `hermes.session.id` | string | Hermes session ID |
| `session.id` | string | Standard OTel alias |
| `user.id` | string | Hermes user ID (optional) |

Set at **end** (turn summary):

| Attribute | Type | Meaning |
|---|---|---|
| `hermes.turn.tool_count` | int | Distinct tool names invoked |
| `hermes.turn.tools` | string | Sorted CSV of distinct tool names (≤500 chars) |
| `hermes.turn.tool_targets` | string | `\|`-joined distinct file paths/URLs |
| `hermes.turn.tool_commands` | string | `\|`-joined distinct shell commands |
| `hermes.turn.tool_outcomes` | string | Sorted CSV of distinct outcome statuses |
| `hermes.turn.skill_count` | int | Distinct skills inferred |
| `hermes.turn.skills` | string | Sorted CSV of distinct skill names |
| `hermes.turn.api_call_count` | int | `pre_api_request` hooks fired |
| `hermes.turn.final_status` | string | `completed` · `interrupted` · `incomplete` · `timed_out` |

Empty/zero aggregators are omitted.

## `llm.*`

Span kind: `LLM` (OpenInference).

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `llm.model_name` | OpenInference | string | Model name |
| `llm.provider` | OpenInference | string | Provider (anthropic, openai, ...) |
| `gen_ai.request.model` | gen_ai | string | Model name (Langfuse) |
| `gen_ai.system` | gen_ai | string | Provider (Langfuse) |
| `input.value` | OpenInference | string | User message OR full conversation JSON |
| `input.mime_type` | OpenInference | string | `text/plain` OR `application/json` |
| `output.value` | OpenInference | string | Final assistant response |
| `output.mime_type` | OpenInference | string | `text/plain` |
| `gen_ai.content.prompt` | gen_ai | string | User message |
| `gen_ai.content.completion` | gen_ai | string | Assistant response |
| `hermes.conversation.message_count` | hermes | int | When conversation capture is on (optional) |

## `api.*`

Span kind: `LLM` (OpenInference).

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `gen_ai.request.model` | gen_ai | string | Model name |
| `llm.model_name` | OpenInference | string | Model name |
| `llm.provider` | OpenInference | string | Provider |
| `llm.token_count.prompt` | OpenInference | int | Prompt tokens |
| `llm.token_count.completion` | OpenInference | int | Completion tokens |
| `llm.token_count.total` | OpenInference | int | Sum |
| `llm.token_count.cache_read` | OpenInference | int | Cache read (optional) |
| `llm.token_count.cache_write` | OpenInference | int | Cache write (optional) |
| `gen_ai.usage.input_tokens` | gen_ai | int | Prompt tokens |
| `gen_ai.usage.output_tokens` | gen_ai | int | Completion tokens |
| `gen_ai.usage.cache_read_input_tokens` | gen_ai | int | Cache read (optional) |
| `gen_ai.usage.cache_creation_input_tokens` | gen_ai | int | Cache write (optional) |
| `llm.invocation_parameters` | OpenInference | string (JSON) | Request params |
| `gen_ai.response.finish_reason` | gen_ai | string | `stop`, `tool_use`, `length`, etc. |
| `http.duration_ms` | hermes | int | Wall-clock HTTP duration |

### `api.*` on failure (`api_request_error`)

When the request fails, the span ends with `StatusCode.ERROR`, an `exception`
event (`exception.type` / `exception.message` / `exception.escaped`), and:

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `error.type` | OTel | string | Error class reported by Hermes (e.g. `RateLimitError`) |
| `http.response.status_code` | OTel | int | HTTP status (omitted for network errors) |
| `gen_ai.response.status_code` | gen_ai | int | Same value, gen_ai spelling |
| `hermes.retry.count` | hermes | int | Retries attempted so far for this request |
| `hermes.max_retries` | hermes | int | Configured retry ceiling |
| `hermes.retryable` | hermes | bool | Whether the error is retryable |
| `llm.response.duration_ms` | hermes | float | Wall-clock of the failed attempt |

The most recent `error.type` is also stamped on the turn's root `agent` span at
`on_session_end`.

## `tool.*`

Span kind: `TOOL` (OpenInference).

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `tool.name` | OpenInference | string | Tool name |
| `input.value` | OpenInference | string | Tool args (JSON) |
| `output.value` | OpenInference | string | Tool result |
| `hermes.tool.target` | hermes | string | Inferred file path / URL (optional) |
| `hermes.tool.command` | hermes | string | Inferred shell command (optional) |
| `hermes.tool.outcome` | hermes | string | `completed` · `error` · `timeout` · `blocked` |
| `hermes.skill.name` | hermes | string | Inferred skill name (optional) |

## `subagent.*`

Span kind: `AGENT` (OpenInference). One per delegated child agent; nests under the parent turn, with the child's own root span nested beneath it (or linked, cross-process).

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `gen_ai.operation.name` | gen_ai | string | `invoke_agent` |
| `gen_ai.agent.name` | gen_ai | string | Child role |
| `hermes.subagent.role` | hermes | string | Child role |
| `hermes.subagent.goal` | hermes | string | Delegated goal (preview) |
| `hermes.subagent.child_session_id` | hermes | string | Child session ID (join key) |
| `hermes.subagent.parent_session_id` | hermes | string | Parent session ID |
| `hermes.subagent.parent_turn_id` | hermes | string | Parent turn ID |
| `hermes.subagent.child_id` | hermes | string | Child sub-agent ID (optional) |
| `hermes.subagent.status` | hermes | string | Reported `child_status` (on stop) |
| `hermes.subagent.duration_ms` | hermes | float | Child wall-clock ms (on stop) |
| `hermes.subagent.summary` | hermes | string | Child result summary (on stop) |

The delegated child's own `agent` root additionally carries `hermes.session.is_subagent=true`, `hermes.subagent.parent_session_id`, and `hermes.subagent.role`.

## Metrics (separate from spans)

Emitted via `PeriodicExportingMetricReader` on backends that support OTLP metrics:

| Metric | Type | Unit | Labels |
|---|---|---|---|
| `hermes.tokens.prompt` | Counter | tokens | `model`, `provider` |
| `hermes.tokens.completion` | Counter | tokens | `model`, `provider` |
| `hermes.tokens.total` | Counter | tokens | `model`, `provider` |
| `hermes.tokens.cache_read` | Counter | tokens | `model`, `provider` |
| `hermes.tokens.cache_write` | Counter | tokens | `model`, `provider` |
| `hermes.tool.calls` | Counter | count | `tool_name`, `outcome` |
| `hermes.tool.duration` | Histogram | ms | `tool_name`, `outcome` |
| `hermes.api.duration` | Histogram | ms | `model`, `provider`, `finish_reason` |
| `hermes.skill.inferred` | Counter | count | `skill_name`, `source` |
| `hermes.sessions` | Counter | count | `kind`, `final_status` |
| `hermes.subagent.count` | Counter | count | `role`, `status` |
| `hermes.subagent.duration` | Histogram | ms | `role` |
| `hermes.api.error.count` | Counter | count | `error_type`, `status_class`, `retryable`, `model`, `provider` |
| `hermes.retry.count` | Counter | count | `model`, `provider` |

`status_class` is bucketed to `2xx`/`3xx`/`4xx`/`5xx`/`network`/`other` to keep
cardinality bounded. `hermes.retry.count` increments once per *retryable*
failure.

Label cardinality is bounded by normalised values (outcomes, finish reasons) and small dimension sets (model, tool name). No user IDs or other high-cardinality labels.
