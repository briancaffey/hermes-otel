---
sidebar_position: 7
title: "Grafana Tempo"
description: "Send Hermes traces to a local Grafana Tempo stack or Grafana Cloud — OTLP-native, traces-only, pairs beautifully with Prometheus."
---

# Grafana Tempo

[Grafana Tempo](https://grafana.com/oss/tempo/) is Grafana Labs' open-source distributed tracing backend. It accepts OTLP/HTTP natively on port 4318 and is usually run as part of a larger Grafana + Prometheus + Tempo stack.

**Signals:** traces only. **Deployment:** local (docker compose) or Grafana Cloud. **Cost:** OSS (self-host) / free tier + paid (cloud).

:::tip Looking for all three signals?
If you want Grafana with **traces + metrics + logs**, use the [**LGTM stack**](/backends/lgtm) instead. It bundles Tempo alongside Loki, Mimir, and an OTel Collector in one container. The `type: tempo` backend covered here is only for standalone Tempo deployments; it's traces-only and won't fan out metrics or logs even if pointed at a collector that accepts them.
:::

## Local stack

The repo ships a two-container stack (Tempo 3.1 single binary with local-disk storage +
Grafana with the Tempo datasource provisioned), trimmed from Tempo's upstream
`example/docker-compose/single-binary`:

```bash
docker compose -f docker-compose/tempo/docker-compose.yaml up -d
```

- Grafana UI: http://localhost:3020 (anonymous admin → Explore → Tempo)
- Tempo API: http://localhost:3210
- OTLP/HTTP: http://localhost:4358 *(moved off 4318 so it coexists with the LGTM stack)*

Then point the plugin at Tempo:

```bash
export OTEL_TEMPO_ENDPOINT="http://localhost:4358/v1/traces"
export OTEL_PROJECT_NAME="hermes-otel-tempo"
```

Two Tempo 3 behaviours worth knowing: search results appear 20-30 s after export, and
Tempo truncates attribute values at 2048 bytes by default, which cuts the full prompt
under `content_capture: full`. The repo's `docker-compose/tempo/tempo.yaml` raises
`distributor.max_attribute_bytes` for that reason; set it on your own Tempo too.

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

## Dashboard

The dashboard's Tempo adapter reads traces from Tempo's query API (`query_port`,
default `3200`). Tempo stores no metrics or logs, but a Tempo entry that names a
`prometheus_url` and/or `loki_url` gets the same metrics and logs surface as
`type: lgtm` — see [Grafana LGTM → Dashboard](/backends/lgtm#dashboard), including
the `search_fetch` over-fetch that keeps paging complete and how a native
TraceQL query interacts with the other search fields.

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
