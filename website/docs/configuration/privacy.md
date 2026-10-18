---
sidebar_position: 5
title: "Privacy mode"
description: "content_capture: off records no prompts, responses, tool arguments or tool results while keeping structural metadata — for shared deployments where content can't leave the process."
---

# Privacy mode

Some of the plugin's most useful telemetry is also the most sensitive: the user's message, the tool arguments, the tool result, the model's response. By default (`content_capture: full`) all of it is recorded, including the complete prompt and response on every `api.*` span. In shared deployments (company production, multi-tenant hosting, anywhere the agent handles data that must not leave the process), you need to stop that at the source.

Privacy mode is one setting that does it.

## The switch

```yaml
# config.yaml
content_capture: "off"
```

Or via env var:

```bash
export HERMES_OTEL_CONTENT_CAPTURE=off
```

`capture_previews: false`, the pre-1.11 spelling, still works and means the same thing. The middle setting, `content_capture: preview`, keeps clipped previews (1,200 characters by default) but drops the complete prompts and responses that `full`, the default, records on `api.*` spans.

## What gets suppressed

When `content_capture: off`:

- The complete request and response on `api.*` spans (`gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, `input.value`, `output.value`)
- `input.value` on `llm.*` spans (user message)
- `output.value` on `llm.*` spans (assistant response)
- `input.value` on `tool.*` spans (tool args)
- `output.value` on `tool.*` spans (tool result)
- `hermes.approval.command` / `hermes.approval.description` on `approval.*` spans (the command awaiting human approval)
- The goal and summary previews on `subagent.*` spans
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
- Approval decisions and wait times (`hermes.approval.choice` / `.granted` / `.timed_out` / `.duration_ms`, `hermes.approval.pattern_key`) — the *outcome* of an approval flows even when the command text is suppressed
- Metrics (all of them — counters and histograms)

So you still get a useful operational view: how many tools ran, which tools they were, how long each took, how many tokens the model burned, and whether the turn completed or timed out. You just don't see the message content.

## Startup banner

When privacy mode is active, the plugin prints a one-line banner so it's not a silent setting:

```text
[hermes-otel] ⚠ content_capture=off — prompts, tool I/O and responses are not recorded
```

If you ever see tool args or user messages in the backend UI that you didn't expect, double-check the banner is present.

## Tool commands and targets

Note that `hermes.tool.command` (the shell command passed to `bash`-family tools) and `hermes.tool.target` (the file path passed to `read_file`, `edit_file`, etc.) are **not** suppressed by privacy mode. They're structured metadata, not free-form user content.

They are clipped to 500 characters. If even command and target are too sensitive for your deployment, [file an issue](https://github.com/briancaffey/hermes-otel/issues) asking for a stricter mode.

## Interaction with `preview_max_chars`

`preview_max_chars` (default: 1200) is a separate cap. It clips long previews and ends them with `...`. In `off` mode it does nothing, since there is nothing to clip. In `preview` and `full` mode a clipped preview carries `hermes.preview.<input|output>.truncated = true` and `.original_chars`, so a short-looking value can be told from a clipped one.

## Logs and events

The logs signal is off by default on both of its switches. When on:

- **Forwarded log records** (`logs.capture`) are not filtered by privacy mode: a body is whatever Hermes or a library logged. Secrets are redacted before export with Hermes' own `agent.redact` (the redactor behind `agent.log`) or the plugin's built-in set, and Hermes' host-internal record attributes (`hermes_home`, `session_tag`) never leave. Other sensitive text flows unless the logging application redacts it; narrow with `logs.exclude_loggers`, `logs.logger_levels` or `logs.only_in_turn`.
- **Events** (`logs.events.enabled`) follow privacy mode: `logs.events.content: inherit` means `content_capture: off` puts no prompt, response or tool content on any event, `preview` clips it, `full` sends what the span has. A backend entry's `logs: {events: {content: off}}` removes content for that backend alone, so a SaaS log sink can stay content-free while a local one keeps everything.
- The dashboard's live store holds the same records as the backends, redacted the same way, in a file created owner-only.

See [Logs and events](/configuration/logs#what-never-leaves-the-machine) for the measured redaction cost and the attribution rules.

## Verifying

To check privacy mode: run a Hermes turn that uses a tool, then inspect the trace. Every `input.value`, `output.value`, `gen_ai.input.messages` and `gen_ai.output.messages` attribute should be **absent** (not empty: absent). Token counts and tool names should still be there. The dashboard's **Settings** tab also shows the mode in force.

In Langfuse, you'll see observations with empty input/output panels. In Phoenix, the Input/Output panels won't render at all for those spans.
