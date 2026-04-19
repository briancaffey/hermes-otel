---
sidebar_position: 6
title: "Jaeger"
description: "Send Hermes traces to a local Jaeger container — the classic distributed-tracing UI, OSS, no account required."
---

# Jaeger

[Jaeger](https://www.jaegertracing.io) is the original distributed-tracing UI from Uber. Modern versions (≥ 1.35) accept OTLP/HTTP natively — no collector needed in the middle.

**Signals:** traces only. **Deployment:** local (single container). **Cost:** OSS, no account needed.

## Local setup

The plugin ships a single-container compose file:

```bash
cd ~/.hermes/plugins/hermes_otel
docker compose -f docker-compose/jaeger.yaml up -d
```

Then:

```bash
export OTEL_JAEGER_ENDPOINT="http://localhost:4318/v1/traces"
export OTEL_PROJECT_NAME="hermes-otel-jaeger"
```

UI at http://localhost:16686.

## Multi-backend config

```yaml
backends:
  - type: jaeger
    endpoint: http://localhost:4318/v1/traces
```

## What you'll see

Jaeger shows the plugin's spans as a standard trace tree — the service-map view picks up the parent/child relationships between `session`, `llm`, `api`, and `tool` spans. Each span's attributes are rendered in the "Tags" panel (a long list with everything the plugin emitted).

Jaeger is not LLM-specific, so:

- Message content appears as the raw `input.value` / `gen_ai.content.prompt` tag rather than pretty-printed.
- Token counts are tags, not first-class fields — look for `gen_ai.usage.input_tokens` / `llm.token_count.prompt`.
- Tool args / results are JSON in the `input.value` / `output.value` tags.

For LLM-native UI, pair Jaeger with a fan-out to Phoenix or Langfuse — see [Multi-backend](/backends/multi-backend).

## Metrics caveat

Jaeger is **traces-only** — no OTLP metrics ingest. The plugin auto-detects this and skips the metrics exporter when Jaeger is the only backend.

If you want the plugin's token / tool / cost metrics alongside Jaeger traces, route metrics to a Prometheus-compatible sink via a separate backend entry, or fan out to Phoenix / SigNoz in parallel.

## Troubleshooting

**"Spans are arriving but the service-map is empty"**

- Jaeger builds the service-map from `service.name` on the resource. The plugin sets this from `OTEL_PROJECT_NAME`. If it's unset, every span ends up on an "unknown-service" node — set the env var and restart.

**"OTLP export 404s"**

- Check your Jaeger version. OTLP ingest was added in 1.35. Older Jaegers need the separate `jaeger-collector` sidecar. The bundled compose uses a modern version.

**"The path format looks wrong"**

- Jaeger's OTLP endpoint is `/v1/traces` (exactly like Phoenix, SigNoz, etc.). If you see `/api/traces`, you're looking at the **query** API, not the **ingest** API. The plugin writes to the ingest endpoint.
