---
sidebar_position: 2
title: "Span hierarchy"
description: "What each span type represents, how they nest, and the attributes each one carries."
---

# Span hierarchy

Every Hermes turn produces a nested tree of spans. This page documents what each one is for and the data it carries.

## The tree

```text
agent / cron                                  [AGENT]
├── skill.{name}                              [SKILL] (load → turn end; overlaps OK)
└── llm.{model}                               [LLM]
    ├── api.{model}                           [LLM]
    │   ├── tool.{name}                       [TOOL]
    │   ├── tool.skill_view                   [TOOL] (the call that *loads* skill.{name})
    │   ├── tool.{name}                       [TOOL] (parallel tool calls — siblings)
    │   ├── tool.delegate_task                [TOOL] (the tool call that delegates)
    │   └── subagent.{role}                   [AGENT] (delegation span)
    │       └── agent (child session root)    [AGENT] (the child rejoins this trace)
    │           └── llm.{model} → api.{model} → tool.{name} ...   (child's own work)
    └── api.{model}                           [LLM]  (second round-trip after tool results)
```

- **`agent` / `cron`** — the root for each turn. Present when session hooks are available in the Hermes build; absent on older versions (the `llm.*` span becomes the root).
- **`skill.*`** — one per skill loaded during the turn. Spans from the load (a `skill_view` call or a `/skills/` read) to the turn boundary, nested under the turn root. Skills overlap freely — two loaded in one turn are two concurrent siblings. See [`skill.*`](#skill) below.
- **`llm.*`** — one per logical model turn. Wraps one or more HTTP round-trips to the provider.
- **`api.*`** — one per HTTP round-trip. Tools run during a round-trip, so their parent is `api.*`, not `llm.*`.
- **`tool.*`** — one per tool invocation. Parallel tool calls are siblings under the same `api.*`.
- **`subagent.*`** — one per delegated child agent. The child's own root span nests under it so a multi-agent run is one connected trace. See [`subagent.*`](#subagent) below.

## `session.*` / `cron`

The root span. Name is derived from the Hermes session kind (`session.cli`, `session.telegram`, `session.discord`, `cron`, etc.).

**Span kind:** `GENERAL` (no OpenInference-specific kind).

Key attributes, set at start:

| Attribute | Source |
|---|---|
| `openinference.project.name` | `OTEL_PROJECT_NAME` / `HERMES_OTEL_PROJECT_NAME` |
| `hermes.session.kind` | From Hermes (`cli`, `telegram`, `cron`, etc.) |
| `hermes.session.id` | Hermes session ID |
| `session.id` | Same as above, standard OTel naming |
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

**Span kind:** `LLM` (OpenInference).

| Attribute | Convention | Meaning |
|---|---|---|
| `llm.model_name` | OpenInference | Model name |
| `llm.provider` | OpenInference | Provider (anthropic, openai, etc.) |
| `input.value` | OpenInference | User message *or* full conversation history (see below) |
| `input.mime_type` | OpenInference | `text/plain` or `application/json` |
| `output.value` | OpenInference | Final assistant response |
| `output.mime_type` | OpenInference | `text/plain` |
| `gen_ai.request.model` | gen_ai | Model name (for Langfuse / SigNoz) |
| `gen_ai.content.prompt` | gen_ai | User message (same content as `input.value` when both are strings) |
| `gen_ai.content.completion` | gen_ai | Assistant response |
| `hermes.conversation.message_count` | hermes-specific | When `capture_conversation_history: true` |

By default `input.value` is the latest user turn only. To see the full message list the model saw, enable [conversation capture](/configuration/conversation-capture).

## `api.*`

One per HTTP round-trip to the provider. Name is `api.{model}`.

**Span kind:** `LLM` (OpenInference).

| Attribute | Convention | Meaning |
|---|---|---|
| `llm.token_count.prompt` | OpenInference | Prompt tokens |
| `llm.token_count.completion` | OpenInference | Completion tokens |
| `llm.token_count.total` | OpenInference | Sum of the above |
| `llm.token_count.cache_read` | OpenInference | Prompt tokens read from cache (if provider reports) |
| `llm.token_count.cache_write` | OpenInference | Prompt tokens written to cache (if provider reports) |
| `gen_ai.usage.input_tokens` | gen_ai | Prompt tokens (for Langfuse) |
| `gen_ai.usage.output_tokens` | gen_ai | Completion tokens (for Langfuse) |
| `gen_ai.usage.cache_read_input_tokens` | gen_ai | Cache read (if provider reports) |
| `gen_ai.usage.cache_creation_input_tokens` | gen_ai | Cache write (if provider reports) |
| `llm.invocation_parameters` | OpenInference | JSON of temperature, max_tokens, etc. |
| `gen_ai.response.finish_reason` | gen_ai | `stop`, `tool_use`, `length`, etc. |
| `http.duration_ms` | hermes-specific | Wall-clock duration of the HTTP call |

The `api.*` span is the right place to look for token counts — not the parent `llm.*` (which doesn't carry per-call counts, because a turn can have multiple `api.*` calls).

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

## `skill.*` {#skill}

One per skill the agent loads during a turn. A skill isn't a tool call — it's
loaded once (the agent calls `skill_view`, or reads a `/skills/<name>/` file)
and then *guides the rest of the turn*. So the span opens at the load and
closes at the **turn boundary** (`on_session_end`), nested under the turn's
`agent` root rather than the in-flight tool/LLM span. That makes "which skills
were active, and for how long" a visible part of the timeline.

Skills **overlap**: load two in one turn and you get two concurrent `skill.*`
siblings — loading a skill again in the same turn keeps the first window (no
duplicate span). Controlled by `skill_spans` (default on); the
`hermes.skill.name` attribute on the tool span and the `skill_inferred` counter
are emitted regardless.

| Attribute | Convention | Meaning |
|---|---|---|
| `hermes.skill.name` | hermes-specific | Skill name |
| `gen_ai.skill.name` | gen_ai (ext.) | Skill name (GenAI-convention alias) |
| `hermes.skill.source` | hermes-specific | `skill_view` (canonical) or `path_match` |
| `hermes.skill.path` | hermes-specific | Conventional `~/.hermes/skills/<name>` location |
| `hermes.skill.result_status` | hermes-specific | Turn outcome: `completed` · `interrupted` · `incomplete` |
| `hermes.span_kind` | hermes-specific | `skill` (for UI grouping) |

Status is always `OK` — a skill being active is never itself an error; the
turn's outcome rides on `hermes.skill.result_status`.

## `subagent.*` {#subagent}

One per delegated child agent (when the parent calls the `delegate_task` tool). Name is `subagent.{role}` (e.g. `subagent.leaf`, `subagent.researcher`).

**Span kind:** `AGENT` (OpenInference).

The delegation span opens on `subagent_start` and nests under the parent turn's in-flight `api.*`/`llm.*` span. Crucially, the delegated child's **own root span rejoins this trace**: when the child runs in the same process (the default), its `agent` root nests directly under the `subagent.*` span, so a multi-agent run is a single connected tree instead of many disconnected traces.

| Attribute | Convention | Meaning |
|---|---|---|
| `gen_ai.operation.name` | gen_ai | `invoke_agent` |
| `gen_ai.agent.name` | gen_ai | The child's role |
| `hermes.subagent.role` | hermes-specific | Child role (`leaf`, `orchestrator`, …) |
| `hermes.subagent.goal` | hermes-specific | What the child was asked to do (preview) |
| `hermes.subagent.child_session_id` | hermes-specific | The child's session ID (join key) |
| `hermes.subagent.parent_session_id` | hermes-specific | The delegating parent's session ID |
| `hermes.subagent.parent_turn_id` | hermes-specific | The parent turn that delegated |
| `hermes.subagent.child_id` | hermes-specific | The child's sub-agent ID |
| `hermes.subagent.status` | hermes-specific | Reported `child_status` (set on stop) |
| `hermes.subagent.duration_ms` | hermes-specific | Child wall-clock duration (set on stop) |
| `hermes.subagent.summary` | hermes-specific | The child's result summary (set on stop) |

The child's `agent` root carries `hermes.session.is_subagent=true` plus `hermes.subagent.parent_session_id` / `hermes.subagent.role` so you can filter child runs even when looking at a single span.

**Status:** failure-like `child_status` values (`error`, `failed`, `cancelled`, `timeout`) map the span to `ERROR`; anything else (including a missing status) stays `OK`.

**Metrics:** `hermes.subagent.count{role, status}` and `hermes.subagent.duration{role}`.

See [Hooks reference](/reference/hooks#subagent_start) for the rejoin mechanism and the cross-process span-link fallback.

## Why this shape?

The tree mirrors the agent's execution structure:

- **One root per turn** so you can filter "one user question worth of work" in the backend UI.
- **`llm.*` as a logical parent of all `api.*`** because the conversation-with-the-model is one coherent thing even when it takes multiple HTTP calls.
- **`tool.*` under `api.*`** because tools run *between* rounds of model inference, within a specific HTTP response's tool_calls. The `api.*` parent makes that explicit.

See [Attribute conventions](/architecture/attributes) for the dual-convention mapping side-by-side.
