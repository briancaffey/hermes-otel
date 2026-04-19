---
sidebar_position: 4
title: "Hooks reference"
description: "The eight Hermes lifecycle hooks this plugin subscribes to, and the span operation each performs."
---

# Hooks reference

hermes-otel subscribes to eight Hermes lifecycle hooks. Six are "always available" on any Hermes version with plugin support. Two are newer (session hooks) and are registered conditionally.

## Always available

### `pre_tool_call`

Fires just before Hermes runs a tool.

- **Span op:** `tracker.start("tool.{name}", parent=current_api or current_llm)`
- **Attributes set on start:** `tool.name`, `input.value` (args JSON), `hermes.tool.target`, `hermes.tool.command`, `hermes.skill.name`
- **Side effects:** increments the session aggregator (`session_state`) for turn summary; orphan sweep runs first

### `post_tool_call`

Fires when the tool returns (success, error, or timeout).

- **Span op:** closes the `tool.*` span
- **Attributes set on end:** `output.value` (result), `hermes.tool.outcome`
- **Span status:** `ERROR` if outcome is `error`, else `OK`
- **Metrics:** `hermes.tool.calls{tool_name, outcome}` counter, `hermes.tool.duration{tool_name, outcome}` histogram

### `pre_llm_call`

Fires before the logical LLM turn starts (before any HTTP round-trips).

- **Span op:** `tracker.start("llm.{model}", parent=session_root)`
- **Attributes set on start:** `llm.model_name`, `llm.provider`, `gen_ai.request.model`, and (when `capture_conversation_history: true`) `input.value` JSON + `input.mime_type=application/json` + `hermes.conversation.message_count`
- **Side effects:** stores the current `llm.*` as the parent for subsequent `api.*` and `tool.*`

### `post_llm_call`

Fires after the logical LLM turn finishes (all round-trips done, final response received).

- **Span op:** closes the `llm.*` span
- **Attributes set on end:** `output.value` (final assistant response), `gen_ai.content.completion`

### `pre_api_request`

Fires before each HTTP request to the LLM provider. Can fire multiple times per `llm.*` turn.

- **Span op:** `tracker.start("api.{model}", parent=current_llm)`
- **Attributes set on start:** `llm.model_name`, `llm.provider`, `llm.invocation_parameters`
- **Side effects:** increments `hermes.turn.api_call_count`

### `post_api_request`

Fires when the HTTP response is parsed.

- **Span op:** closes the `api.*` span
- **Attributes set on end:** token counts (both conventions), `gen_ai.response.finish_reason`, `http.duration_ms`
- **Metrics:** `hermes.tokens.*` counters, `hermes.api.duration` histogram

## Newer (session hooks)

Registered inside a `try:/except:` because older Hermes versions don't expose them. If they're missing, the plugin logs a debug message and degrades gracefully to 6-hook operation.

### `on_session_start`

Fires at the start of a user turn (CLI input, inbound message, cron wake-up).

- **Span op:** `tracker.start("session.{kind}", parent=None)` — this becomes the root of the trace
- **Attributes set on start:** `hermes.session.kind`, `hermes.session.id`, `session.id`, `user.id`, `openinference.project.name`
- **Fallback if not available:** the `llm.*` span becomes the root; turn summary is attached there instead of on a dedicated session root

### `on_session_end`

Fires when the turn is fully complete (assistant has returned its final response, interrupted, or timed out).

- **Span op:** closes the `session.*` span
- **Attributes set on end:** the full [turn summary](/architecture/turn-summary) — `hermes.turn.tool_count`, `hermes.turn.tools`, `hermes.turn.tool_targets`, `hermes.turn.tool_commands`, `hermes.turn.tool_outcomes`, `hermes.turn.skill_count`, `hermes.turn.skills`, `hermes.turn.api_call_count`, `hermes.turn.final_status`
- **Metrics:** `hermes.sessions{kind, final_status}` counter
- **Side effects:** if `force_flush_on_session_end: true` (default), synchronously force-flushes every `BatchSpanProcessor` so the trace appears in the backend UI immediately

## Hook → span mapping

```text
Hermes hook              OTel operation
──────────────────────   ─────────────────────────────────────
on_session_start         open  session.{kind}
pre_llm_call             open  llm.{model}       (child of session)
pre_api_request          open  api.{model}       (child of llm)
pre_tool_call            open  tool.{name}       (child of api or llm)
post_tool_call           close tool.{name}
post_api_request         close api.{model}
post_llm_call            close llm.{model}
on_session_end           close session.{kind}    + turn summary + force-flush
```

## Parallel tool calls

When the model emits multiple tool calls in a single response, Hermes fires `pre_tool_call` / `post_tool_call` for each in sequence (or in parallel, depending on the Hermes version). The plugin handles both: each tool span gets its own `span.start` / `span.end`, and they're all children of the same `api.*` parent.

See `hooks.py` for the actual callback implementations and the `SpanTracker` class for the parent-stack management.
