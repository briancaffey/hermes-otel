---
sidebar_position: 3
title: "Attribute conventions"
description: "Canonical OpenTelemetry GenAI attributes plus Hermes extensions."
---

# Attribute conventions

hermes-otel emits the OpenTelemetry GenAI convention for LLM telemetry (`gen_ai.*`) and uses `hermes.*` for Hermes-specific extensions. Generic `input.value` / `output.value` are still used for preview-safe input and output rendering.

## Token counts (on `api.*` spans)

| Metric | GenAI attribute |
|---|---|
| Prompt tokens | `gen_ai.usage.input_tokens` |
| Completion tokens | `gen_ai.usage.output_tokens` |
| Cache read | `gen_ai.usage.cache_read.input_tokens` |
| Cache write | `gen_ai.usage.cache_creation.input_tokens` |

Cache read/write are only populated when the provider reports them (Anthropic's prompt caching, OpenAI's — both surface them in their API responses).
Total tokens are derived downstream from input + output tokens rather than emitted as a duplicate span attribute.

## Message content (on `llm.*` spans)

| Attribute | Meaning |
|---|---|
| `input.value` | Preview-safe user message or full conversation JSON |
| `input.mime_type` | `text/plain` or `application/json` |
| `output.value` | Preview-safe assistant response |
| `output.mime_type` | `text/plain` |
| `gen_ai.input.messages` | Full input messages when explicitly enabled |
| `gen_ai.output.messages` | Full output messages when explicitly enabled |
| `gen_ai.system_instructions` | System prompt when explicitly enabled |

When [conversation capture](/configuration/conversation-capture) is on, `input.value` becomes JSON of the full message list, `input.mime_type` becomes `application/json`, and `hermes.conversation.message_count` records how many messages were passed.

## Model / request metadata

| Attribute | Meaning |
|---|---|
| `gen_ai.request.model` | Requested model name |
| `gen_ai.response.model` | Provider-returned model name |
| `gen_ai.provider.name` | Provider (anthropic, openai, ...) |
| `gen_ai.operation.name` | `chat`, `invoke_agent`, `execute_tool`, etc. |
| `gen_ai.request.temperature`, `gen_ai.request.max_tokens`, ... | Request params when available |
| `gen_ai.response.finish_reasons` | Finish reasons such as `stop`, `tool_use`, `length` |

## Tool spans

Tool-span attributes are largely OpenInference-native (the `gen_ai.*` convention doesn't have tool-specific names yet). Both backends index on them:

| Attribute | Meaning |
|---|---|
| `tool.name` | Tool name |
| `input.value` | Tool args (JSON string) |
| `output.value` | Tool result (string) |
| `hermes.tool.target` | Inferred file / URL (plugin-specific) |
| `hermes.tool.command` | Inferred shell command (plugin-specific) |
| `hermes.tool.outcome` | `completed` / `error` / `timeout` / `blocked` (plugin-specific) |
| `hermes.skill.name` | Inferred skill name (plugin-specific, optional) |

See [Tool identity](/architecture/tool-identity).

## Session / turn metadata (on `session.*`)

| Attribute | Meaning |
|---|---|
| `openinference.project.name` | Project name from `OTEL_PROJECT_NAME` |
| `gen_ai.conversation.id` | Hermes session/conversation ID |
| `gen_ai.conversation.compacted` | `true` when Hermes explicitly reports compaction; otherwise unset |
| `gen_ai.agent.name` | Active Hermes profile / agent name |
| `gen_ai.operation.name` | `invoke_agent` for session spans |
| `hermes.session.kind` | `cli` / `telegram` / `discord` / `cron` / ... (plugin-specific) |
| `user.id` | Hermes user ID |
| `hermes.turn.*` | Turn summary (see [Turn summary](/architecture/turn-summary)) |

## Resource-level attributes

Set on the OTel `Resource` and therefore stamped on **every** span:

| Attribute | Source |
|---|---|
| `service.name` | `OTEL_PROJECT_NAME` (falls back to `"hermes-agent"`) |
| `service.version` | `hermes-otel` plugin version |
| `otel.scope.name` | `hermes-otel` |
| `openinference.project.name` | Same as `service.name` |
| *plus* any `resource_attributes:` / `global_tags:` from `config.yaml` |

## Compatibility note

Older hermes-otel releases emitted OpenInference `llm.*` aliases alongside GenAI fields. Current releases prefer the GenAI names only for model/provider/token metadata; update backend queries and collector dimensions to use the `gen_ai.*` keys above.

## Roadmap

- [OpenInference Tool span kind is now stable](https://arize.com/docs/phoenix/reference/openinference) — already emitted.
- `gen_ai.tool.*` convention is evolving; we'll add it once the spec is stable.
- Session identity uses `gen_ai.conversation.id`; older `session.id` / `hermes.session.id` aliases are intentionally not dual-emitted.
