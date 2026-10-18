---
sidebar_position: 5
title: "SigNoz"
description: "Send Hermes traces and metrics to SigNoz — self-hosted or cloud — one of the few LLM-friendly OSS stacks that covers traces + metrics + logs."
---

# SigNoz

[SigNoz](https://signoz.io) is an open-source observability platform that speaks OTLP natively across all three signals. It's one of the few OSS options that unifies traces, metrics, and logs in a single UI.

**Signals:** traces + metrics + logs. **Deployment:** local (docker compose) or cloud. **Cost:** OSS (self-host) / free tier + paid (cloud).

## Self-hosted

A ready-to-use compose file ships with the plugin. Upstream SigNoz listens for OTLP/HTTP on the standard port 4318, which collides with any other collector on the host (the LGTM stack, for one). The bundled compose file moves it to **4328**, and the UI from 8080 to **3301**:

```bash
cd ~/.hermes/plugins/hermes_otel
docker compose -f docker-compose/signoz/docker-compose.yaml up -d
```

Then:

```bash
export OTEL_SIGNOZ_ENDPOINT="http://localhost:4328/v1/traces"
```

UI at http://localhost:3301. Before SigNoz accepts any data you must register the first admin user; see [Troubleshooting](#troubleshooting).

## SigNoz Cloud

```bash
export OTEL_SIGNOZ_ENDPOINT="https://ingest.us.signoz.cloud:443/v1/traces"
export OTEL_SIGNOZ_INGESTION_KEY="sz-..."
```

When `OTEL_SIGNOZ_INGESTION_KEY` is set, the plugin sends it as the `signoz-ingestion-key` header on the traces, metrics and logs exporters. Metrics go out as delta, the `signoz` type's preset.

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

- `hermes.token.usage` (counter, by `token_type`)
- `hermes.model.usage` (counter: completed messages per model and provider)
- `gen_ai.execute_tool.duration` (histogram, seconds; the deprecated `hermes.tool.duration` in milliseconds is still sent)
- `gen_ai.client.operation.duration` (histogram, seconds)

Full list: [Metrics reference](/reference/metrics).

See [Span attributes reference](/reference/span-attributes) for the full list.

## Dashboard

The bundled dashboard's SigNoz adapter (and the `observability` skill with
`--source signoz`) reads traces, **metrics and logs** back through SigNoz's
query API. It needs two extra keys on the backend entry, because the query API
sits on the UI port and requires authentication even when ingestion does not:

```yaml
  - type: signoz
    endpoint: http://localhost:4328/v1/traces
    query_port: 3301                 # the SigNoz UI/API port (443 behind an HTTPS ingress)
    api_key_env: SIGNOZ_API_KEY      # a PAT or a session JWT; sent as both SIGNOZ-API-KEY and Bearer
```

Metrics are read with the query builder (`/api/v4/query_range`): counters such
as `hermes.token.usage` are shown as the **increase per bucket** (delta and
cumulative alike), gauges as the per-bucket reduction; the instrument list comes
from the metrics autocomplete API, without histogram `.bucket`/`.min`/`.max`
internals. Logs filter on `trace_id`, `hermes.session_id`, the logger (SigNoz's
`scope_name`), severity and body text; the logger list is a `count` grouped by
`scope_name`.

## Attribute convention

SigNoz reads `gen_ai.*` attributes for LLM-specific views, which the plugin emits alongside the OpenInference `llm.*` convention. Both sets land on the same spans — SigNoz uses whichever it recognises.

## Troubleshooting

**"Connection refused on 4318 right after a fresh self-hosted install"**

- The SigNoz collector opens its receivers only after it has registered with
  the SigNoz server over OpAMP, and that registration fails until the first
  admin user (and with it the organisation) exists — `signoz` logs
  `failed to find or create agent` on every heartbeat. Register the admin in
  the UI (or `POST /api/v1/register`), and ingestion starts within a minute.
  Until then every export is a silently dropped batch.

**"Port 4318 is already in use"**

- 4318 is the standard OTLP/HTTP port, so any other collector on the host (the LGTM stack, a Collector) may already hold it. The bundled compose file moves SigNoz to 4328 (`SIGNOZ_OTLP_HTTP_PORT`). If you've customised it, double-check the port.

**"Cloud ingestion: 401 / missing key"**

- The ingestion key is required for SigNoz Cloud. The header is `signoz-ingestion-key`, not `Authorization`. The plugin sets it automatically when `OTEL_SIGNOZ_INGESTION_KEY` is defined.

**"Metrics show up, traces don't"**

- `OTEL_SIGNOZ_ENDPOINT` (or `endpoint:`) is the traces URL and must end in `/v1/traces`; the plugin derives the `/v1/metrics` and `/v1/logs` URLs from it. If you set a custom endpoint in `hermes_otel.yaml`, double-check the path.
