# Hermes Agent Observability with OpenTelemetry: One Plugin, Three Backends

If you are building with Hermes Agent, you probably care about more than just final outputs. You want to see what happened during a run:

- Which model calls were made?
- Which tools were called?
- How many tokens were used?
- Where did errors happen?

This plugin, `hermes_otel`, adds that visibility by converting Hermes lifecycle events into OpenTelemetry spans and exporting them to tracing backends like Phoenix, Langfuse, and LangSmith.

## Why this plugin exists

Agent behavior is often hard to debug because execution is layered:

1. User message enters the agent loop.
2. The LLM may make one or more API calls.
3. Tools can execute in between those model calls.
4. The final answer is produced after one or many internal steps.

Without structured traces, this can feel like a black box.

`hermes_otel` gives you a timeline of that process with parent-child span relationships so you can inspect both the high-level flow and low-level details.

## What gets traced

The plugin hooks into Hermes events and creates spans for:

- `llm.{model}` for model turns
- `api.{model}` for individual provider API requests
- `tool.{name}` for each tool invocation

Typical shape:

```text
LLM span
  API span (request 1)
    Tool span(s)
  API span (request 2)
```

This gives you a clean view of multi-step reasoning loops where the model calls tools and then resumes generation.

## Backend support

The plugin supports three modes with automatic detection:

1. **LangSmith** (highest priority when enabled)
2. **Langfuse** (if keys are provided)
3. **Phoenix / generic OTLP** (fallback via `OTEL_ENDPOINT`)

That means one tracing implementation can target different observability stacks depending on environment variables.

## Why cross-backend mapping matters

A tricky part of observability is that different systems prefer different attribute conventions.

This plugin emits dual conventions so traces are usable across tools:

- OpenInference style keys (great for Phoenix)
- `gen_ai.*` usage keys (recognized by Langfuse and modern GenAI OTel pipelines)

For token accounting, the plugin records prompt, completion, and total usage on API spans and propagates those values into backend-friendly formats.

## LangSmith details

LangSmith support is implemented through its HTTP run APIs:

- `POST /runs` to start spans
- `PATCH /runs/{id}` to finish spans

During span close, token usage is normalized and sent in both common formats:

- top-level token fields
- `usage_metadata` token fields

This improves compatibility with current LangSmith ingestion and UI rendering expectations.

## Langfuse details

Langfuse is supported through OTLP HTTP export with:

- Basic Auth from public/secret keys
- ingestion header `x-langfuse-ingestion-version: 4`
- endpoint `/api/public/otel` (or signal-specific variant)

The plugin supports both:

- plugin-specific env vars (`OTEL_LANGFUSE_*`)
- Langfuse standard env vars (`LANGFUSE_*`)

This makes setup easier whether you are configuring directly for Hermes or reusing existing Langfuse environment settings.

## Design choices worth noting

### 1. Synchronous export for reliability

The plugin uses a simple synchronous processor to reduce trace loss risk in short-lived or abrupt processes. This can add small per-span latency, but favors correctness and debuggability.

### 2. Parent/child span tracking

Hooks fire independently, so the plugin keeps an in-memory span tracker to reconnect `pre_*` and `post_*` events and preserve nesting.

### 3. Safe defaults and fallbacks

If no backend is configured, the plugin remains disabled and avoids failing agent execution.

### 4. Debug logging is opt-in

Debug file logging is behind `HERMES_OTEL_DEBUG=true`, so normal runs stay clean while troubleshooting remains easy.

## Quick setup examples

### Phoenix

```bash
export OTEL_ENDPOINT="http://localhost:6006/v1/traces"
export OTEL_PROJECT_NAME="hermes-agent"
```

### Langfuse (standard vars)

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
```

### LangSmith

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY="lsv2_..."
export LANGSMITH_PROJECT="hermes-langsmith-otel"
```

## What this unlocks in practice

Once enabled, you can answer practical operational questions faster:

- Why did this turn take 14 seconds?
- Which tool call failed?
- Did token usage spike after a prompt change?
- Is a provider/model change increasing latency or cost?

For teams, this also improves collaboration between app engineers, prompt engineers, and infra engineers because everyone can inspect the same execution graph.

## Final take

`hermes_otel` turns Hermes Agent from a black box into an observable system. It captures model calls, API calls, and tool calls as structured spans, maps data across multiple tracing ecosystems, and keeps configuration flexible enough to work in local dev and production.

If you are deploying agent workflows seriously, this kind of tracing is not a nice-to-have. It is foundational.

