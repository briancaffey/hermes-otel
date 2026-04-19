---
sidebar_position: 5
title: "SigNoz"
description: "Send Hermes traces and metrics to SigNoz — self-hosted or cloud — one of the few LLM-friendly OSS stacks that covers traces + metrics + logs."
---

# SigNoz

[SigNoz](https://signoz.io) is an open-source observability platform that speaks OTLP natively across all three signals. It's one of the few OSS options that unifies traces, metrics, and logs in a single UI.

**Signals:** traces + metrics + logs. **Deployment:** local (docker compose) or cloud. **Cost:** OSS (self-host) / free tier + paid (cloud).

## Self-hosted

A ready-to-use compose file ships with the plugin. The upstream SigNoz stack uses port 4318 for OTLP/HTTP, which collides with Phoenix — the bundled compose remaps SigNoz's OTLP HTTP port to **4328** to avoid that:

```bash
cd ~/.hermes/plugins/hermes_otel
docker compose -f docker-compose/signoz/docker-compose.yaml up -d
```

Then:

```bash
export OTEL_SIGNOZ_ENDPOINT="http://localhost:4328/v1/traces"
export OTEL_PROJECT_NAME="hermes-agent"
```

UI at http://localhost:3301 (SigNoz's default port).

## SigNoz Cloud

```bash
export OTEL_SIGNOZ_ENDPOINT="https://ingest.us.signoz.cloud:443/v1/traces"
export OTEL_SIGNOZ_INGESTION_KEY="sz-..."
```

When `OTEL_SIGNOZ_INGESTION_KEY` is set, the plugin attaches the `signoz-ingestion-key` header to both the traces and metrics exporters.

Regional endpoints:

- US: `https://ingest.us.signoz.cloud:443/v1/traces`
- EU: `https://ingest.eu.signoz.cloud:443/v1/traces`
- India: `https://ingest.in.signoz.cloud:443/v1/traces`

## Multi-backend config

```yaml
backends:
  - type: signoz
    endpoint: http://localhost:4328/v1/traces
    # SigNoz Cloud only — ignored by self-hosted
    ingestion_key_env: OTEL_SIGNOZ_INGESTION_KEY
```

## What you'll see

SigNoz treats the plugin's spans as standard OTel traces. The service-map view lights up with the `session → llm → api → tool` edges, and the trace detail panel shows the nested span tree with the full attribute payload.

Metrics flow to the SigNoz metrics UI automatically:

- `hermes.tokens.prompt` / `hermes.tokens.completion` (counters)
- `hermes.tool.calls` (counter)
- `hermes.tool.duration` (histogram)
- `hermes.api.duration` (histogram)

See [Span attributes reference](/reference/span-attributes) for the full list.

## Attribute convention

SigNoz reads `gen_ai.*` attributes for LLM-specific views, which the plugin emits alongside the OpenInference `llm.*` convention. Both sets land on the same spans — SigNoz uses whichever it recognises.

## Troubleshooting

**"Port 4318 is already in use"**

- Phoenix and SigNoz both default to 4318 for OTLP/HTTP. The bundled compose file remaps SigNoz to 4328. If you've customised it, double-check the port.

**"Cloud ingestion: 401 / missing key"**

- The ingestion key is required for SigNoz Cloud. The header is `signoz-ingestion-key`, not `Authorization`. The plugin sets it automatically when `OTEL_SIGNOZ_INGESTION_KEY` is defined.

**"Metrics show up, traces don't"**

- You might be pointing metrics at the right endpoint but traces at the wrong one. `OTEL_SIGNOZ_ENDPOINT` is for traces; metrics go to a parallel `/v1/metrics` path which the plugin derives automatically from the trace endpoint. If you've set a fully custom endpoint via `config.yaml`, double-check the path.
