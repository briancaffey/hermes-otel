---
sidebar_position: 3
title: "Concepts"
description: "How the plugin maps Hermes lifecycle hooks to OpenTelemetry spans, and why the span tree looks the way it does."
---

# Concepts

Hermes fires lifecycle hooks (callbacks at fixed points of a turn); this plugin turns them into OpenTelemetry spans, metrics and logs. Read this page once and the rest of the docs will read as reference.

## Hermes hooks → OTel spans

Hermes fires lifecycle events as it works through a turn. hermes-otel subscribes to 34 of them (the list is `provides_hooks` in `plugin.yaml`; see the [hooks reference](/reference/hooks)). These eight build the core span tree:

| Hook | When it fires | Span it creates / ends |
|---|---|---|
| `on_session_start` | Start of a session (CLI, Telegram, cron, etc.) | Opens the turn root, named `agent` (or `cron` for a cron job) |
| `pre_llm_call` | Hermes is about to send a prompt to the model | Opens `llm.{model}` |
| `pre_api_request` | Right before an HTTP request to the provider | Opens `api.{model}` |
| `post_api_request` | HTTP response received | Closes `api.{model}`, attaches token counts |
| `pre_tool_call` | Hermes is about to run a tool | Opens `tool.{name}` |
| `post_tool_call` | Tool finished (or errored) | Closes `tool.{name}`, attaches result |
| `post_llm_call` | LLM call resolved | Closes `llm.{model}` |
| `on_session_end` | Turn complete | Closes the `agent` / `cron` root, attaches turn summary, force-flushes |

Because `pre_*` opens and `post_*` closes, the span tree naturally nests:

```text
agent
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

That's two `api.*` calls but one logical `llm.*` turn. Tools run during a round-trip, so a `tool.*` span is a child of the `api.*` span that asked for it. The parent `llm.*` span carries the user message (input) and the final assistant response (output), so at a glance you see what the user asked and what they got back. By default each `api.*` span also carries the complete request and response (see [Conversation capture](/configuration/conversation-capture)).

## Dual-convention attributes

Observability vendors use different attribute names for the same LLM facts. hermes-otel writes **both** the OpenTelemetry GenAI convention (`gen_ai.*`, read by Langfuse and most OTel tools) and the [OpenInference](https://arize.com/docs/phoenix/reference/openinference) convention (`llm.*`, read by Phoenix) on the same span, so whichever backend you point at finds the names it expects.

See [Attribute conventions](/architecture/attributes) for the full mapping.

## Non-blocking export

Ending a span puts it on a queue; it does not make a network call. A background `BatchSpanProcessor` worker drains the queue in batches every second and sends them over OTLP/HTTP (or OTLP/gRPC for a `type: otlp` entry with `protocol: grpc`). A slow collector makes the queue grow; it does not block your tool call.

If the agent produces spans faster than the exporter sends them and the bounded queue fills up, the oldest spans are dropped and Hermes keeps running. At the end of each turn the plugin also flushes the queues from a background thread, so the trace shows up right away. See [Batch export tuning](/configuration/batch-export).

## Multi-backend fan-out

One span tree, several destinations:

```yaml
# ~/.hermes/hermes_otel.yaml
backends:
  - type: phoenix
    endpoint: http://localhost:6006/v1/traces
  - type: langfuse
    public_key_env: LANGFUSE_PUBLIC_KEY
    secret_key_env: LANGFUSE_SECRET_KEY
  - type: jaeger
    endpoint: http://localhost:4318/v1/traces
```

Each backend gets its own `BatchSpanProcessor` with its own worker thread and queue. A slow or unreachable collector only affects its own queue. At startup the plugin also opens one TCP connection to each endpoint and logs whether it is reachable ([`probe_backends`](/reference/config-schema)).

## Privacy mode

The agent's work is full of user text: prompts, tool arguments, tool results. By default (`content_capture: full`) the plugin records it, including the complete prompt and response on every `api.*` span. For shared deployments where that content must not leave the process:

```yaml
content_capture: "off"
```

The content attributes (`input.value`, `output.value`, `gen_ai.input.messages` and the rest) are then never set. Metadata (tool names, durations, token counts, outcomes) still flows, so dashboards keep working. See [Privacy mode](/configuration/privacy).

## What's emitted vs. what's not

**Emitted today:**

- Span tree (`agent` / `llm` / `api` / `tool`, plus skills, approvals, sub-agents and kanban)
- Token counts (prompt / completion / cache read / cache write)
- Model name, provider, finish reason
- Tool name + args + result + outcome
- The complete prompt and response on each `api.*` span (`content_capture: full`, the default)
- Per-turn summary (tool count, skill count, API count, final status)
- Metrics (counters + histograms), exported every `flush_interval_ms` (60 s by default) and at the end of each turn
- Logs and structured events, when you turn them on ([Logs and events](/configuration/logs))

**Not emitted:**

- OTLP/HTTP with JSON bodies. HTTP exports use protobuf (`http/protobuf`); OTLP/gRPC is available only on a generic `type: otlp` entry ([gRPC and TLS](/backends/otlp#grpc-and-tls)).

See [Limitations](/reference/limitations) for the full list.

## Next

- **[Pick a backend](/backends/overview)** — the comparison table
- **[Span hierarchy](/architecture/span-hierarchy)** — what each span carries, verbatim
- **[Config reference](/reference/config-schema)** — every knob, every env var
