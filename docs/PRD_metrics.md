# PRD: OpenTelemetry Metrics Export for hermes-otel

## Overview

This document describes adding first-class OpenTelemetry Metrics support to the hermes-otel plugin, enabling automated collection and export of operational metrics (session counts, token usage, costs, latency, etc.) alongside the existing trace data.

### Goals

1. Export metrics via OTLP to configured backends (Phoenix, Langfuse)
2. Capture operational metrics relevant to LLM agent usage and cost attribution
3. Enable downstream analysis in Grafana, Phoenix dashboards, or custom tooling

### Non-Goals

- UI/dashboard building (metrics stored in backend for external visualization)
- Modifying existing trace semantics

---

## Metrics Specification

| Metric | Type | Description |
|--------|------|-------------|
| `hermes.session.count` | Counter | Sessions created (monotonic, cumulative) |
| `hermes.token.usage` | Counter | Tokens by type: input, output, reasoning, cacheRead, cacheCreation |
| `hermes.cost.usage` | Counter | USD cost per completed assistant message |
| `hermes.lines_of_code.count` | Counter | Lines added/removed per session.diff event |
| `hermes.commit.count` | Counter | Git commits via bash tool invocation |
| `hermes.tool.duration` | Histogram | Tool execution time in milliseconds |
| `hermes.cache.count` | Counter | Cache activity: cacheRead or cacheCreation |
| `hermes.session.duration` | Histogram | Session duration created→idle (ms) |
| `hermes.message.count` | Counter | Completed assistant messages per session |
| `hermes.session.token.total` | Histogram | Total tokens per session on idle |
| `hermes.session.cost.total` | Histogram | Total USD cost per session on idle |
| `hermes.model.usage` | Counter | Messages per model + provider |
| `hermes.retry.count` | Counter | API retries via session.status events |

### Attribute Dimensions

Metrics carry attributes for dimensional slice-and-dice:

- `session_id` — unique session identifier
- `model` — model name (e.g., gpt-4o)
- `provider` — provider platform (e.g., anthropic, openai)
- `tool_name` — for tool.duration, tool.name attribute
- `token_type` — for token.usage: input|output|reasoning|cacheRead|cacheCreation
- `cache_type` — for cache.count: cacheRead|cacheCreation

---

## Technical Design

### Dependencies

Current dependencies are sufficient:

```toml
dependencies = [
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-http",
]
```

The same `OTLPMetricExporter` re-uses the existing OTLP-over-HTTP transport.

### Architecture

```
┌─────────────────────────────────────────────────────┐
│                 HermesOTelPlugin                     │
│  ┌─────────────────┐  ┌──────────────────┐   │
│  │ TracerProvider   │  │  MeterProvider  │   │
│  │ (exists)       │  │  (NEW)          │   │
│  └─────────────────┘  └──────────────────┘   │
│                                                │
│  SpanTracker ──────► MetricInstruments          │
│  (exists)             (new counters/histograms)│
└─────────────────────────────────────────────────────┘
                      │
                      ▼
            OTLPMetricExporter (HTTP)
                      │
                      ▼
         Configured Endpoint (Phoenix/Langfuse)
```

### Implementation Phases

1. **Initialize MeterProvider** — share Resource with TracerProvider
2. **Create instrument instances** — module-level counters/histograms in `tracer.py`
3. **Emit from hooks** — add metric recordings in `hooks.py`
4. **Session-scoped aggregation** — same pattern as token totals for costs/durations

### Export Behavior

- **Counter** — monotonic, cumulative; incremented directly on events
- **Histogram** — records values; OTel SDK aggregates (count, sum, min, max, histogram buckets)

Phoenix stores OTLP metrics in Aurora (ADGM). Langfuse stores usage data independently. Custom dashboards query the backend directly.

---

## Metric Event Sources

| Metric | Hook / Source |
|--------|--------------|
| session.count | `on_session_start` (new session created) |
| token.usage | `on_post_llm_call` / `on_post_api_request` (usage dict) |
| cost.usage | `on_post_api_request` (usage dict with cost field) |
| lines_of_code.count | New hook: `on_session_diff` (lines added/removed) |
| commit.count | New hook: bash tool detection of `git commit` |
| tool.duration | `on_pre_tool_call` → `on_post_tool_call` (duration via args) |
| cache.count | `on_post_api_request` (cache_read_tokens / cache_write_tokens) |
| session.duration | `on_session_start` → `on_session_idle` (timestamp tracking) |
| message.count | `on_post_llm_call` (per session) |
| session.token.total | `on_session_idle` (aggregated from _SESSION_USAGE) |
| session.cost.total | `on_session_idle` (aggregated costs) |
| model.usage | `on_post_api_request` (per model + provider) |
| retry.count | New hook: `on_session_status` (status=retry) |

### New Hook Requirements

- **on_session_idle** — fires when session becomes idle; records session-duration, token/cost totals
- **on_session_diff** — fires on diff output; passes lines added/removed
- **session.status** — existing hook to detect retry status

---

## Visualization

Metrics export to the configured backend (Phoenix/Langfuse) via OTLP. Visualization requires:

1. **Phoenix** — metrics stored in Aurora; build custom Grafana dashboards pointing to Phoenix Athena/ADGM, or use Phoenix's built-in metrics view if available
2. **Langfuse** — limited custom metric display; core usage tracked natively; custom metrics require backend query access

The plugin handles collection/export only. Dashboard creation is out of scope.

---

## Backward Compatibility

- Metrics export only if MeterProvider initialization succeeds
- Trace behavior unchanged
- No new required environment variables

---

## Testing Plan

1. Unit test metric instrument creation
2. Integration test with mock OTel endpoint
3. Verify metric export to live Phoenix/Langfuse endpoint and query via backend

---

## Timeline

| Phase | Effort |
|-------|--------|
| MeterProvider init + instrument registry | 1-2 days |
| Emit from existing hooks | 1-2 days |
| New hook handling (idle/diff/status) | 1-2 days |
| Integration testing | 1 day |
| **Total** | **4-7 days** |