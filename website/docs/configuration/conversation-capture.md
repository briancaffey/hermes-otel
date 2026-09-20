---
sidebar_position: 6
title: "Conversation capture"
description: "content_capture: full (the default), preview or off — what of the prompts, tool I/O and responses lands on spans, and how to see the complete conversation the model saw."
---

# Conversation capture

One setting decides how much of the conversation content the plugin records:

```yaml
# hermes_otel.yaml
content_capture: "full"     # full (default) · preview · off
```

Or `HERMES_OTEL_CONTENT_CAPTURE=full|preview|off`. Quote the value in YAML: a bare `off` is YAML for `false` (the plugin accepts it and reads it as `off`, but `"off"` is clearer).

| Mode | `api.*` spans (one per model call) | `llm.*`, `tool.*`, `approval.*`, `subagent.*` spans | Use it when |
|---|---|---|---|
| **`full`** (default) | The complete request as sent to the provider (system prompt, every message, every tool result) and the complete response, unclipped | Clipped previews (`preview_max_chars`, 1200 by default) | You own the backend and want to answer "what did the model actually see and say?" |
| `preview` | No content | Clipped previews, with `hermes.preview.*.truncated` markers when clipped | Storage or bandwidth is tight |
| `off` | No content | No content; names, timings, tokens and status only | Content must not leave the process, see [Privacy mode](/configuration/privacy) |

The dashboard's **Settings** tab shows the mode in force and where it was set.

## What `full` writes

On every `api.*` span, once per attribute convention so both Phoenix (OpenInference) and Langfuse or any OTel GenAI reader render it, and never a third copy:

| Attribute | Content |
|---|---|
| `gen_ai.input.messages`, `input.value` (`input.mime_type: application/json`) | The message list exactly as sent: system prompt first, then the history, tool calls and tool results |
| `gen_ai.system_instructions` | The system prompt on its own: the leading `system` message, or the Responses API `instructions` |
| `gen_ai.output.messages`, `output.value` | The assistant's reply: its text (`text/plain`) and, inside the one assistant message, the tool calls it made; tool calls alone become the JSON `output.value` |
| `hermes.content.input_chars`, `hermes.content.output_chars` | Sizes, so a backend can chart payload growth without parsing the payload |

Intermediate calls inside a tool loop are included, so "why did the model call that tool?" is answerable from the span that made the call. Phoenix shows the messages in its Input and Output panels; Langfuse in the observation's input and output.

Two things to know:

- **Nothing is clipped by the plugin.** Hermes hands the hook both a sanitised copy of the request, where long strings are cut and end in `...[truncated N chars]`, and the raw `request_messages` list it actually sent. The plugin reads the raw list, so a 15,000-character system prompt arrives whole. A `[truncated N chars]` marker inside captured content means the value came through a payload only the sanitised copy carried; raise `HERMES_PLUGIN_PAYLOAD_MAX_CHARS` in Hermes's environment.
- **It is large and verbatim.** Each `api.*` span in a turn repeats the whole prefix, so a long turn stores the conversation several times over, and it is stored on every configured backend. The export batch drops to 64 spans per POST automatically (`span_batch_max_export_batch_size` overrides). See [Limitations](/reference/limitations#full-content-capture-is-the-default-and-large).

Tool spans keep previews in every mode: in `full` mode the complete tool result is already inside the next `api.*` span's message list, as the model saw it. MCP tool spans (`mcp_*`) are the exception and carry their full arguments and result.

## Previews and the truncation markers

Outside `api.*` spans, content is a preview: ANSI stripped, whitespace collapsed, clipped at `preview_max_chars` (or the per-category `tool_input_preview_max_chars`, `tool_output_preview_max_chars`, `llm_input_preview_max_chars`, `llm_output_preview_max_chars`). When a preview was clipped the span also carries `hermes.preview.input.truncated: true` and `hermes.preview.input.original_chars` (or `output`), so a short-looking value is distinguishable from a clipped one.

## The turn on the `llm.*` span

`capture_conversation_history: true` additionally attaches the conversation as Hermes held it at the start of the turn to the `llm.*` span (`input.value` as JSON, capped at `conversation_history_max_chars`, 20,000 by default). It predates `content_capture: full` and is mostly redundant with it; keep it for backends where you look at the turn span rather than the per-call spans.

```yaml
capture_conversation_history: true
conversation_history_max_chars: 40000
```

## The pre-1.11 keys

`capture_previews`, `capture_full_prompts` and `capture_full_responses` still work and are kept consistent with `content_capture`: `capture_previews: false` is `off`; either full flag `true` is `full`; a full flag `false` while the other is `true` keeps that side as previews. When `content_capture` is set it wins and a conflicting legacy key is warned about. New configs should use `content_capture` only.
