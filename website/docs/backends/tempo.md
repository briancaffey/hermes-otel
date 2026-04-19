---
sidebar_position: 7
title: "Grafana Tempo"
description: "Send Hermes traces to a local Grafana Tempo stack or Grafana Cloud — OTLP-native, traces-only, pairs beautifully with Prometheus."
---

# Grafana Tempo

[Grafana Tempo](https://grafana.com/oss/tempo/) is Grafana Labs' open-source distributed tracing backend. It accepts OTLP/HTTP natively on port 4318 and is usually run as part of a larger Grafana + Prometheus + Tempo stack.

**Signals:** traces only. **Deployment:** local (docker compose) or Grafana Cloud. **Cost:** OSS (self-host) / free tier + paid (cloud).

## Local stack

Tempo ships an upstream single-binary docker-compose example that bundles Tempo + MinIO (S3) + Grafana + Prometheus:

```bash
cd ~/git/grafana/tempo/example/docker-compose/single-binary
docker compose up -d
```

- Grafana UI: http://localhost:3000 (anonymous admin)
- OTLP/HTTP: http://localhost:4318
- OTLP/gRPC: http://localhost:4317 *(unused — the plugin is HTTP/JSON only)*

Then point the plugin at Tempo:

```bash
export OTEL_TEMPO_ENDPOINT="http://localhost:4318/v1/traces"
export OTEL_PROJECT_NAME="hermes-otel-tempo"
```

Open Grafana, pick the pre-configured Tempo data source, and query — the span tree renders in Grafana's Explore view.

## Grafana Cloud

Grafana Cloud has a free tier that includes Tempo. Grab the OTLP endpoint + auth from Grafana Cloud's OpenTelemetry setup page:

```bash
export OTEL_TEMPO_ENDPOINT="https://tempo-prod-04-eu-west-0.grafana.net/otlp/v1/traces"
```

Grafana Cloud uses HTTP Basic Auth; pass it via `config.yaml` headers:

```yaml
backends:
  - type: tempo
    endpoint: https://tempo-prod-04-eu-west-0.grafana.net/otlp/v1/traces
    headers:
      Authorization: "Basic ${GRAFANA_CLOUD_TOKEN}"
```

## What you'll see

Tempo is a pure trace store; the UI lives in Grafana. In Grafana's Explore view, the plugin's span tree appears with the usual `session → llm → api → tool` nesting, and each span's attributes show as key-value tags in the detail panel.

Because Tempo is traces-only, the LLM-native views (token-count panels, cost attribution) aren't available unless you pair it with Prometheus for the metrics — which the upstream compose example already sets up.

## Metrics caveat

Tempo is **traces-only** — no OTLP metrics ingest. The plugin auto-skips the metrics exporter when Tempo is the only backend.

The upstream example bundles Prometheus. To route the plugin's metrics there:

1. Run an OTel collector alongside Tempo that receives metrics and writes them to Prometheus via `remote_write`.
2. Or configure Tempo with a sidecar collector (see Grafana's docs).
3. Or fan out to SigNoz / Phoenix in parallel via [Multi-backend](/backends/multi-backend).

## Multi-backend config

```yaml
backends:
  - type: tempo
    endpoint: http://localhost:4318/v1/traces
```

## Troubleshooting

**"Grafana can't find traces"**

- Grafana needs the Tempo data source configured. The upstream compose example preconfigures this; if you're using a custom stack, add Tempo as a data source at `http://tempo:3200` (the query port, not the ingest port).

**"Traces arrive but service name is 'unknown-service'"**

- Set `OTEL_PROJECT_NAME` so the plugin can stamp `service.name` on the resource. Grafana's Tempo views group traces by service name.

**"OTLP export hangs"**

- Grafana Cloud's OTLP endpoint requires TLS and Basic Auth. A missing or malformed `Authorization` header shows up as a hang because the Go OTLP client retries by default. Double-check the base64 encoding.
