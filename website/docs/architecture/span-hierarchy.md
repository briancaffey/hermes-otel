---
sidebar_position: 2
title: "Span hierarchy"
description: "What each span type represents, how they nest, and the attributes each one carries."
---

# Span hierarchy

Every Hermes turn produces a nested tree of spans. This page documents what each one is for and the data it carries.

## The tree

```text
session.{platform} / cron                     [GENERAL]
└── llm.{model}                               [LLM]
    ├── api.{model}                           [LLM]
    │   └── tool.{name}                       [TOOL]
    │   └── tool.{name}                       [TOOL] (parallel tool calls — siblings)
    └── api.{model}                           [LLM]  (second round-trip after tool results)
```

- **`session.*` / `cron`** — the root for each turn. Present when session hooks are available in the Hermes build; absent on older versions (the `llm.*` span becomes the root).
- **`llm.*`** — one per logical model turn. Wraps one or more HTTP round-trips to the provider.
- **`api.*`** — one per HTTP round-trip. Tools run during a round-trip, so their parent is `api.*`, not `llm.*`.
- **`tool.*`** — one per tool invocation. Parallel tool calls are siblings under the same `api.*`.

## `session.*` / `cron`

The root span. Name is derived from the Hermes session kind (`session.cli`, `session.telegram`, `session.discord`, `cron`, etc.).

**Span kind:** `GENERAL` (no OpenInference-specific kind).

Key attributes, set at start:

| Attribute | Source |
|---|---|
| `openinference.project.name` | `OTEL_PROJECT_NAME` / `HERMES_OTEL_PROJECT_NAME` |
| `hermes.session.kind` | From Hermes (`cli`, `telegram`, `cron`, etc.) |
| `gen_ai.conversation.id` | Hermes session/conversation ID |
| `user.id` | Hermes user ID (when available) |

Key attributes, set at end (the **turn summary**):

| Attribute | Type | Meaning |
|---|---|---|
| `hermes.turn.tool_count` | int | Distinct tool names invoked |
| `hermes.turn.tools` | string | Sorted CSV of distinct tool names (≤500 chars) |
| `hermes.turn.tool_targets` | string | `\|`-joined distinct file paths / URLs |
| `hermes.turn.tool_commands` | string | `\|`-joined distinct shell commands |
| `hermes.turn.tool_outcomes` | string | Sorted CSV of distinct outcome statuses |
| `hermes.turn.skill_count` | int | Distinct skills inferred |
| `hermes.turn.skills` | string | Sorted CSV of distinct skill names |
| `hermes.turn.api_call_count` | int | Number of `pre_api_request` hooks fired |
| `hermes.turn.final_status` | string | `completed` · `interrupted` · `incomplete` · `timed_out` |

See [Turn summary](/architecture/turn-summary) for why these exist.

## `llm.*`

One per logical model turn. Name is `llm.{model}` (e.g. `llm.claude-sonnet-4-6`).

**Span kind:** LLM-like model turn.

| Attribute | Convention | Meaning |
|---|---|---|
| `gen_ai.request.model` | gen_ai | Model name |
| `gen_ai.provider.name` | gen_ai | Provider (anthropic, openai, etc.) |
| `gen_ai.operation.name` | gen_ai | Operation name such as `chat` |
| `input.value` | generic | User message *or* full conversation history (see below) |
| `input.mime_type` | generic | `text/plain` or `application/json` |
| `output.value` | generic | Final assistant response |
| `output.mime_type` | generic | `text/plain` |
| `gen_ai.input.messages` | gen_ai | Full input messages when explicitly enabled |
| `gen_ai.output.messages` | gen_ai | Full output messages when explicitly enabled |
| `hermes.conversation.message_count` | hermes-specific | When `capture_conversation_history: true` |

By default `input.value` is the latest user turn only. To see the full message list the model saw, enable [conversation capture](/configuration/conversation-capture).

## `api.*`

One per HTTP round-trip to the provider. Name is `api.{model}`.

**Span kind:** LLM-like provider request.

| Attribute | Convention | Meaning |
|---|---|---|
| `gen_ai.usage.input_tokens` | gen_ai | Prompt tokens |
| `gen_ai.usage.output_tokens` | gen_ai | Completion tokens |
| `gen_ai.usage.cache_read.input_tokens` | gen_ai | Prompt tokens read from cache (if provider reports) |
| `gen_ai.usage.cache_creation.input_tokens` | gen_ai | Prompt tokens written to cache (if provider reports) |
| `gen_ai.request.*` | gen_ai | Request params such as temperature and max tokens |
| `gen_ai.response.finish_reasons` | gen_ai | `stop`, `tool_use`, `length`, etc. |

The `api.*` span is the right place to look for token counts — not the parent `llm.*` (which doesn't carry per-call counts, because a turn can have multiple `api.*` calls). Total tokens are derived downstream from input + output tokens rather than emitted as a duplicate span attribute; API duration is emitted as the `gen_ai.client.operation.duration` metric.

## `tool.*`

One per tool invocation. Name is `tool.{name}` (e.g. `tool.bash`, `tool.read_file`).

**Span kind:** `TOOL` (OpenInference).

| Attribute | Convention | Meaning |
|---|---|---|
| `tool.name` | OpenInference | Tool name |
| `input.value` | OpenInference | Tool args (JSON) |
| `output.value` | OpenInference | Tool result |
| `hermes.tool.target` | hermes-specific | Inferred file path / URL (see [Tool identity](/architecture/tool-identity)) |
| `hermes.tool.command` | hermes-specific | Inferred shell command |
| `hermes.tool.outcome` | hermes-specific | `completed` · `error` · `timeout` · `blocked` |
| `hermes.skill.name` | hermes-specific | Skill inferred from args paths (optional) |

Errors: `hermes.tool.outcome=error` also maps the span's `StatusCode` to `ERROR`. Timeouts and blocked tools stay `OK` so dashboards don't count them as failures.

## Why this shape?

The tree mirrors the agent's execution structure:

- **One root per turn** so you can filter "one user question worth of work" in the backend UI.
- **`llm.*` as a logical parent of all `api.*`** because the conversation-with-the-model is one coherent thing even when it takes multiple HTTP calls.
- **`tool.*` under `api.*`** because tools run *between* rounds of model inference, within a specific HTTP response's tool_calls. The `api.*` parent makes that explicit.

See [Attribute conventions](/architecture/attributes) for the GenAI attribute mapping.
