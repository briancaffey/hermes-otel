---
sidebar_position: 5
title: "Privacy mode"
description: "Suppress all input/output previews (user messages, tool args, tool results) while keeping structural metadata — for shared deployments where content can't leave the process."
---

# Privacy mode

Some of the plugin's most valuable telemetry is also the most sensitive: the user's message, the tool arguments, the tool result, the model's response. In shared deployments (company production, multi-tenant hosting, anywhere the agent operates on data that can't leave the process), you often need to suppress all of that at the source.

Privacy mode is a single knob that does exactly that.

## The switch

```yaml
# config.yaml
capture_previews: false
```

Or via env var:

```bash
export HERMES_OTEL_CAPTURE_PREVIEWS=false
```

## What gets suppressed

When `capture_previews: false`:

- `input.value` on `llm.*` spans (user message)
- `output.value` on `llm.*` spans (assistant response)
- `gen_ai.content.prompt` / `gen_ai.content.completion` on `llm.*` spans
- `input.value` on `tool.*` spans (tool args)
- `output.value` on `tool.*` spans (tool result)
- The conversation-history JSON when [conversation capture](/configuration/conversation-capture) is also enabled

These attributes are **never set** on the span — not "set and then redacted". A reader can't pull them back out.

## What still flows

Everything that isn't user-originated content:

- Span tree (parent/child relationships)
- Span timings (start / end / duration)
- Tool names, commands, targets, outcomes (`tool.name`, `hermes.tool.command`, `hermes.tool.target`, `hermes.tool.outcome`)
- Token counts (`gen_ai.usage.*`, `llm.token_count.*`)
- Model name, provider, finish reason
- Per-turn summary (tool count, skill count, API call count, final status)
- Metrics (all of them — counters and histograms)

So you still get a useful operational view: how many tools ran, which tools they were, how long each took, how many tokens the model burned, and whether the turn completed or timed out. You just don't see the message content.

## Startup banner

When privacy mode is active, the plugin prints a one-line banner so it's not a silent setting:

```text
[hermes-otel] ▲ Privacy mode: input/output previews suppressed (capture_previews=false)
```

If you ever see tool args or user messages in the backend UI that you didn't expect, double-check the banner is present.

## Tool commands and targets

Note that `hermes.tool.command` (the shell command passed to `bash`-family tools) and `hermes.tool.target` (the file path passed to `read_file`, `edit_file`, etc.) are **not** suppressed by privacy mode. They're structured metadata, not free-form user content.

If even command and target are too sensitive for your deployment, file an issue — we can add a stricter mode.

## Interaction with `preview_max_chars`

`preview_max_chars` (default: 1200) is a separate truncation cap. It clips long previews with a `...`. When `capture_previews: false`, `preview_max_chars` becomes a no-op — there's nothing to clip.

## Verifying

A quick way to verify privacy mode is working: run a Hermes turn that uses a tool, then inspect the trace. Every `input.value` / `output.value` / `gen_ai.content.*` attribute should be **absent** (not empty — absent). Token counts and tool names should still be present.

In Langfuse, you'll see observations with empty input/output panels. In Phoenix, the Input/Output panels won't render at all for those spans.
