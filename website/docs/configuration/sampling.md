---
sidebar_position: 4
title: "Sampling"
description: "How to sample a fraction of traces — ParentBased(TraceIdRatioBased) so you always see whole traces, never partials."
---

# Sampling

Full telemetry on every turn is rarely what you want in production — it's expensive and it makes the backend UI cluttered. Sampling lets you keep a random fraction of traces while discarding the rest at the source.

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

Valid range: `0.0` to `1.0`. Setting `0.0` disables all spans (equivalent to `enabled: false` but cheaper — the decision is made per-span in the SDK).

## Why ParentBased?

The plugin configures [`ParentBased(TraceIdRatioBased(rate))`](https://opentelemetry.io/docs/specs/otel/trace/sdk/#parentbased). What that means:

- The **root span** of each trace is evaluated against the rate (a random decision seeded by the trace ID).
- **Descendant spans** inherit the root's decision — if the root is sampled, they all are; if it isn't, none of them are.

You never see a partial trace where half the children were sampled and half weren't. Either the whole `session → llm → api → tool` tree makes it to the backend or none of it does.

## When to sample

Rough rules of thumb:

| Situation | Suggested rate |
|---|---|
| Local development | `null` (AlwaysOn) |
| Staging | `1.0` or `null` |
| Low-volume production (< 1 turn/sec) | `null` or `0.5` |
| Medium-volume production (1-10 turns/sec) | `0.1` – `0.25` |
| High-volume production (> 10 turns/sec) | `0.01` – `0.05`, then boost on error |

For the last case (low sample rate + boost on error) you'd need a tail-based sampler — hermes-otel doesn't ship one yet, so the head-based decision is locked in at span start. If you need tail-based sampling, pipe through an OTel Collector that does.

## Sampling and metrics

Sampling only affects **spans**. Metrics (token counts, tool counts, API durations) are always recorded — they're aggregates, not per-trace events, so sampling them would give you wrong numbers.

## Verifying

Debug logging prints the sampler config at startup:

```text
[hermes-otel] Sampler: ParentBased(TraceIdRatioBased(0.25))
```

And each span decision is logged at debug level — see [Debug logging](/development/debug-logging).
