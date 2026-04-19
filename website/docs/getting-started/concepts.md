---
sidebar_position: 3
title: "Concepts"
description: "How the plugin maps Hermes lifecycle hooks to OpenTelemetry spans, and why the span tree looks the way it does."
---

# Concepts

If you've worked with OpenTelemetry before, the surface area here is tiny: Hermes fires lifecycle hooks; this plugin turns each one into a span. Read this page once and the rest of the docs will read as reference.

## Hermes hooks → OTel spans

Hermes emits lifecycle events as it works through a turn. hermes-otel subscribes to eight of them:

| Hook | When it fires | Span it creates / ends |
|---|---|---|
| `on_session_start` | Start of a user turn (CLI, Telegram, cron, etc.) | Opens `session.{platform}` |
| `pre_llm_call` | Hermes is about to send a prompt to the model | Opens `llm.{model}` |
| `pre_api_request` | Right before an HTTP request to the provider | Opens `api.{model}` |
| `post_api_request` | HTTP response received | Closes `api.{model}`, attaches token counts |
| `pre_tool_call` | Hermes is about to run a tool | Opens `tool.{name}` |
| `post_tool_call` | Tool finished (or errored) | Closes `tool.{name}`, attaches result |
| `post_llm_call` | LLM call resolved | Closes `llm.{model}` |
| `on_session_end` | Turn complete | Closes `session.*`, attaches turn summary, force-flushes |

Because `pre_*` opens and `post_*` closes, the span tree naturally nests:

```text
session.cli
└── llm.claude-sonnet-4-6
    ├── api.claude-sonnet-4-6          (round-trip 1: model asks to call a tool)
    │   └── tool.bash                  (tool runs, result returned)
    └── api.claude-sonnet-4-6          (round-trip 2: model sees tool result, answers)
```

## Why two `api.*` spans under one `llm.*`?

Because a single user turn usually involves **multiple HTTP round-trips**. The typical flow:

1. Hermes sends the prompt → model responds with `tool_calls`
2. Hermes runs each tool → sends tool results back
3. Model responds with final text → turn done

That's two `api.*` calls but one logical `llm.*` turn. The parent `llm.*` span carries the user message (input) and the final assistant response (output), so at a glance you see what the user asked and what they got back.

## Dual-convention attributes

Different observability vendors standardised on different attribute names for the same LLM concepts. hermes-otel emits **both** the [Langfuse / `gen_ai.*`](https://langfuse.com/docs) convention and the [Phoenix / OpenInference `llm.*`](https://arize.com/docs/phoenix/reference/openinference) convention on the same span, so whichever backend you point at sees the data it's expecting.

See [Attribute conventions](/architecture/attributes) for the full mapping.

## Non-blocking export

`span.end()` is a **queue push**, not a network call. A background `BatchSpanProcessor` worker drains the queue in batches every second and POSTs over OTLP/HTTP. A slow collector means the queue grows; it does not mean your tool call blocks.

If the agent outruns the exporter (bounded queue fills up), the oldest spans are dropped — Hermes keeps running. See [Batch export tuning](/configuration/batch-export).

## Multi-backend fan-out

One span tree, several destinations:

```yaml
# ~/.hermes/plugins/hermes_otel/config.yaml
backends:
  - type: phoenix
    endpoint: http://localhost:6006/v1/traces
  - type: langfuse
    public_key_env: LANGFUSE_PUBLIC_KEY
    secret_key_env: LANGFUSE_SECRET_KEY
  - type: jaeger
    endpoint: http://localhost:4318/v1/traces
```

Each backend gets its own `BatchSpanProcessor` with an independent worker thread and queue. A slow or unreachable collector only affects its own queue.

## Privacy mode

The agent's work is full of user text — prompts, tool args, tool results. For shared deployments where that content can't leave the process:

```yaml
capture_previews: false
```

Strips every `input.value` / `output.value` at emit time. Metadata (tool names, durations, token counts, outcomes) still flows so dashboards keep working.

## What's emitted vs. what's not

**Emitted today:**

- Span tree (session / llm / api / tool)
- Token counts (prompt / completion / cache read / cache write)
- Model name, provider, finish reason
- Tool name + args + result + outcome
- Per-turn summary (tool count, skill count, API count, final status)
- Metrics (counters + histograms) over `PeriodicExportingMetricReader`

**Not emitted:**

- The fully-formed prompt (system message + conversation history + tool results). Hermes hooks don't currently expose it. The raw user message and final assistant response appear on the parent `llm.*` span; opt into [Conversation capture](/configuration/conversation-capture) to also get the history JSON on the `llm.*` span.
- gRPC export. HTTP/JSON only.

See [Limitations](/reference/limitations) for the full list.

## Next

- **[Pick a backend](/backends/overview)** — the comparison table
- **[Span hierarchy](/architecture/span-hierarchy)** — what each span carries, verbatim
- **[Config reference](/reference/config-schema)** — every knob, every env var
