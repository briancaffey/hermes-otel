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

Label cardinality is bounded by normalised values (outcomes, finish reasons) and small dimension sets (model, tool name). No user IDs or other high-cardinality labels.
