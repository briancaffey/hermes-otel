---
sidebar_position: 3
title: "Span attribute reference"
description: "Every attribute the plugin sets, by span type."
---

# Span attribute reference

Every attribute the plugin may set, grouped by span type. See [Attribute conventions](/architecture/attributes) for the narrative version of the GenAI mapping.

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
| `gen_ai.conversation.id` | string | Hermes session/conversation ID |
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
| `gen_ai.request.model` | gen_ai | string | Model name |
| `gen_ai.provider.name` | gen_ai | string | Provider (anthropic, openai, ...) |
| `gen_ai.operation.name` | gen_ai | string | Operation name |
| `input.value` | generic | string | User message OR full conversation JSON |
| `input.mime_type` | generic | string | `text/plain` OR `application/json` |
| `output.value` | generic | string | Final assistant response |
| `output.mime_type` | generic | string | `text/plain` |
| `gen_ai.input.messages` | gen_ai | array/string | Full input messages when explicitly enabled |
| `gen_ai.output.messages` | gen_ai | array/string | Full output messages when explicitly enabled |
| `hermes.conversation.message_count` | hermes | int | When conversation capture is on (optional) |

## `api.*`

Span kind: `LLM` (OpenInference).

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `gen_ai.request.model` | gen_ai | string | Model name |
| `gen_ai.usage.input_tokens` | gen_ai | int | Prompt tokens |
| `gen_ai.usage.output_tokens` | gen_ai | int | Completion tokens |
| `gen_ai.usage.cache_read.input_tokens` | gen_ai | int | Cache read (optional) |
| `gen_ai.usage.cache_creation.input_tokens` | gen_ai | int | Cache write (optional) |
| `gen_ai.request.*` | gen_ai | mixed | Request params |
| `gen_ai.response.finish_reasons` | gen_ai | string/list | `stop`, `tool_use`, `length`, etc. |

Total tokens are derived downstream from input + output tokens rather than emitted as a duplicate span attribute.

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
| `gen_ai.client.token.usage` | Histogram | `{token}` | `gen_ai.token.type`, `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` |
| `gen_ai.client.operation.duration` | Histogram | s | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` |
| `gen_ai.invoke_agent.duration` | Histogram | s | `gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.provider.name`, `gen_ai.request.model` |
| `gen_ai.execute_tool.duration` | Histogram | s | `gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.tool.name` |
| `hermes.session.count` | Counter | count | `gen_ai.agent.name`, `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` |
| `hermes.cost.usage` | Counter | USD | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` |
| `hermes.message.count` | Counter | count | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` |
| `hermes.model.usage` | Counter | count | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` |
| `hermes.skill.inferred` | Counter | count | `skill_name`, `source` |

Label cardinality is bounded by normalised values (token type, operation name) and small dimension sets (model, provider, tool name). No prompt/message/tool payloads are emitted as metric labels.
