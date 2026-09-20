---
sidebar_position: 6
title: "Conversation capture"
description: "Capture the full message list the model actually saw on the llm.* span — system prompt + history + tool results as JSON."
---

# Conversation capture

By default, the `llm.*` span's `input.value` is just the **latest user turn**. That's the obvious thing to show in a UI, but it's not *what the model actually saw* — the model was given the system prompt, the full conversation history, and all tool results in addition to that last user message.

Turning on `capture_conversation_history` attaches the full message list (as JSON) to the `llm.*` span. Indispensable for debugging "why did the model do that?" questions.

## Enabling it

```yaml
# config.yaml
capture_conversation_history: true
conversation_history_max_chars: 40000   # safety cap
```

Or via env var:

```bash
export HERMES_OTEL_CAPTURE_CONVERSATION_HISTORY=true
```

## What gets set

On every `llm.*` span:

| Attribute | Type | Example |
|---|---|---|
| `input.value` | string | `[{"role":"system","content":"You are..."},{"role":"user","content":"..."}, ...]` |
| `input.mime_type` | string | `application/json` |
| `hermes.conversation.message_count` | int | `12` |

Backends that recognise `input.mime_type=application/json` pretty-print the JSON:

- **Phoenix:** JSON view in the Input panel, fully expandable.
- **Langfuse:** syntax-highlighted JSON blob.
- **SigNoz / Jaeger / Tempo:** raw JSON string — readable but not folded.

## Respects `capture_previews`

When `capture_previews: false` (privacy mode), conversation capture is also suppressed. The two interact cleanly — you don't need to remember to turn this off when you enable privacy mode.

## Respects `preview_max_chars`? Not exactly

The cap on conversation history is **its own field** — `conversation_history_max_chars` — not `preview_max_chars`. The reasoning: conversation JSON is orders of magnitude larger than a single tool input preview, so sharing the same cap would either truncate individual messages uselessly or balloon the size of normal previews.

Default cap is 20,000 characters (≈20 KB UTF-8), which is roughly 5k tokens of conversation. Long conversations get clipped with a trailing `...` on whatever message the cap lands in the middle of.

Bump it for complex agents:

```yaml
conversation_history_max_chars: 100000   # 100 KB
```

## Why only on `llm.*`?

`api.*` spans are per-HTTP-request. A single turn can include multiple `api.*` round-trips (one to get tool calls, another to get the final response after tool results). The conversation history changes between them (tool results get appended), so attaching it to `api.*` spans would double or triple the data with mostly-overlapping payloads.

The parent `llm.*` span represents the whole turn end-to-end. Attaching conversation history there keeps it in one place.

## Performance

Conversation capture adds a JSON serialisation + size check on every `pre_llm_call` hook. For a 10-message conversation at ~200 tokens each, that's ~10 ms of serialisation — negligible next to a network round-trip to the model. Not a concern.

The backend impact is bigger: every trace is now carrying ~20 KB of JSON it didn't carry before. On Langfuse Cloud's free tier (500 MB/mo), that's ~25k turns before you hit the limit. Size accordingly.

## Full prompts and responses on `api.*` spans

`capture_conversation_history` shows the turn on the `llm.*` span, clipped at `conversation_history_max_chars`. To see exactly what each API call sent and received, with no cap at all, turn on the two full-capture flags:

```yaml
# config.yaml
capture_full_prompts: true      # llm.input_messages / gen_ai.input.messages, llm.system_prompt
capture_full_responses: true    # llm.output.content / gen_ai.output.messages, llm.output.tool_calls
```

Or via env vars: `HERMES_OTEL_CAPTURE_FULL_PROMPTS=true` and `HERMES_OTEL_CAPTURE_FULL_RESPONSES=true`.

Every `api.*` span then carries the complete message list the provider received (system prompt, history, tool results) and the complete response. Intermediate calls inside a tool loop are included, so "why did the model call that tool?" is answerable from the span that made the call. Phoenix and Langfuse render these in their Input and Output panels.

Two things to know:

- **Nothing is clipped by the plugin.** Hermes hands the hook both a sanitised copy of the request, where long strings are cut and end in `...[truncated N chars]`, and the raw `request_messages` list it actually sent. The plugin reads the raw list, so a 15,000-character system prompt arrives whole. If you ever see a `[truncated N chars]` marker inside a captured message, it came from a Hermes hook payload the plugin had no uncapped alternative for; raise `HERMES_PLUGIN_PAYLOAD_MAX_CHARS` in Hermes's environment.
- **It is large and verbatim.** Each `api.*` span in a turn repeats the whole prefix, and it is stored on every configured backend. Both flags respect `capture_previews: false`. See [Limitations](../reference/limitations.md#full-prompt-capture-is-opt-in-and-large) and [Privacy mode](./privacy.md).
