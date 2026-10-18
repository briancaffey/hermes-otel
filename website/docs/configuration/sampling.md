---
sidebar_position: 4
title: "Sampling"
description: "How to sample a fraction of traces — ParentBased(TraceIdRatioBased) so you always see whole traces, never partials."
---

# Sampling

Tracing every turn can be expensive in production and crowds the backend UI. Sampling keeps a random fraction of traces and drops the rest before they are exported.

## The default

`sample_rate: null` (or unset) → **AlwaysOn**. Every span is kept. Best for development and low-volume deployments.

## Enabling sampling

```yaml
# config.yaml
sample_rate: 0.25   # keep 25% of traces, drop 75%
```

Or via env var:

```bash
export HERMES_OTEL_SAMPLE_RATE=0.25
```

Valid range: `0.0` to `1.0`; a value outside it logs a warning and falls back to `null`. `0.0` records no traces, but unlike `enabled: false` the hooks still run and metrics are still recorded.

## Why ParentBased?

The plugin configures [`ParentBased(TraceIdRatioBased(rate))`](https://opentelemetry.io/docs/specs/otel/trace/sdk/#parentbased). What that means:

- The **root span** of each trace is evaluated against the rate (a random decision seeded by the trace ID).
- **Descendant spans** inherit the root's decision — if the root is sampled, they all are; if it isn't, none of them are.

You never see a partial trace where half the children were sampled and half weren't. Either the whole `agent → llm → api → tool` tree reaches the backend or none of it does.

When the turn root joins an outer trace (`HERMES_OTEL_TRACEPARENT`, see [`remote_parent`](/configuration/yaml#joining-an-outer-trace-and-the-startup-probe)), the outer span's sampled flag decides instead of the rate.

## When to sample

Rough rules of thumb:

| Situation | Suggested rate |
|---|---|
| Local development | `null` (AlwaysOn) |
| Staging | `1.0` or `null` |
| Low-volume production (< 1 turn/sec) | `null` or `0.5` |
| Medium-volume production (1-10 turns/sec) | `0.1` – `0.25` |
| High-volume production (> 10 turns/sec) | `0.01` – `0.05`, then boost on error |

The last case (a low rate, but keep every error) needs a tail-based sampler, which decides after the trace is complete. hermes-otel decides when the root span starts, so it cannot do this; send through an [OTel Collector](/backends/collector) with tail sampling instead.

## Sampling and metrics

Sampling only affects **spans**. Metrics (token counts, tool counts, API durations) are always recorded: they are aggregates, so sampling them would give wrong numbers.

## Verifying

The dashboard's **Settings** tab shows the `sample_rate` in force and where it was set (file, env var or default). The plugin logs no sampler line at startup and no per-span decision. To check the effect, compare the number of turns you ran with the number of traces in the backend; the live store (the dashboard's Live mode) only holds sampled traces too.
