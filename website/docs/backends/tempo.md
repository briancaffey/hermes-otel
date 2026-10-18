---
sidebar_position: 7
title: "Grafana Tempo"
description: "Send Hermes traces to a local Grafana Tempo stack or Grafana Cloud — OTLP-native, traces-only, pairs beautifully with Prometheus."
---

# Grafana Tempo

[Grafana Tempo](https://grafana.com/oss/tempo/) is Grafana Labs' open-source distributed tracing backend. It accepts OTLP/HTTP natively on port 4318 and is usually run as part of a larger Grafana + Prometheus + Tempo stack.

**Signals:** traces only. **Deployment:** local (docker compose) or Grafana Cloud. **Cost:** OSS (self-host) / free tier + paid (cloud).

:::tip Looking for all three signals?
If you want Grafana with **traces + metrics + logs**, use the [**LGTM stack**](/backends/lgtm) instead. It bundles Tempo alongside Loki, Mimir, and an OTel Collector in one container. The `type: tempo` backend covered here is for standalone Tempo deployments. It sends traces only: metrics and logs stay off unless you set `metrics: true` / `logs: true` on the entry (useful only when a collector that accepts them sits in front).
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

Grafana Cloud uses HTTP Basic Auth. Put the header on a backend entry in `hermes_otel.yaml`; `GRAFANA_CLOUD_TOKEN` here must hold the base64 encoding of `<instance id>:<token>`:

```yaml
backends:
  - type: tempo
    endpoint: https://tempo-prod-04-eu-west-0.grafana.net/otlp/v1/traces
    headers:
      Authorization: "Basic ${GRAFANA_CLOUD_TOKEN}"
```

## What you'll see

Tempo is a pure trace store; the UI lives in Grafana. In Grafana's Explore view, the plugin's span tree appears with the usual `session → llm → api → tool` nesting, and each span's attributes show as key-value tags in the detail panel.

Because Tempo stores traces only, the token and cost metrics need a separate metrics store such as Prometheus. Tempo's upstream compose example includes one; the repo's trimmed stack does not.

## Metrics caveat

Tempo is **traces-only**: it has no OTLP metrics ingest. A `tempo` entry creates no metrics or logs exporter by default.

To keep the plugin's metrics:

1. Run an OTel collector next to Tempo that receives metrics and writes them to Prometheus via `remote_write`.
2. Or fan out to a backend that accepts metrics (SigNoz, LGTM, OpenObserve) in parallel via [Multi-backend](/backends/multi-backend).

## Dashboard

The dashboard's Tempo adapter reads traces from Tempo's query API (`query_port`,
default `3200`; the repo's stack maps it to `3210`, so set `query_port: 3210` there).
Set `ui_url` to a Grafana Explore URL (for example `http://localhost:3020/explore`) to get a
link from each trace to Grafana; `grafana_datasource_uid` names the Tempo data source
(default `tempo`). Tempo stores no metrics or logs, but a Tempo entry that names a
`prometheus_url` and/or `loki_url` gets the same metrics and logs surface as
`type: lgtm` — see [Grafana LGTM → Dashboard](/backends/lgtm#dashboard), including
the `search_fetch` over-fetch that keeps paging complete and how a native
TraceQL query interacts with the other search fields.

## Query over MCP

Traceloop's `opentelemetry-mcp` server reads this stack with `BACKEND_TYPE=tempo BACKEND_URL=http://localhost:3210` (the Tempo HTTP port, not Grafana), so Hermes, Claude Code or Cursor can search the traces and sum token usage over MCP. All eleven tools were verified against a Hermes workload. Setup, observed output and the caveats, including Tempo's base64 span ids: [Query your traces over MCP](/configuration/mcp-query-server).

## Multi-backend config

```yaml
backends:
  - type: tempo
    endpoint: http://localhost:4358/v1/traces   # the repo stack; a stock Tempo listens on 4318
    query_port: 3210                            # dashboard only: the repo stack's Tempo API
```

## Troubleshooting

**"Grafana can't find traces"**

- Grafana needs the Tempo data source configured. The repo's stack provisions it (`grafana-datasources.yaml`); on your own stack, add Tempo as a data source at `http://tempo:3200` (the query port, not the ingest port).

**"Traces arrive under an unexpected service name"**

- The plugin sets `service.name=hermes-agent` on the resource. Change it with `resource_attributes: {service.name: ...}`, or use `service_name_from_profile: true` to name it after the Hermes profile. Grafana's Tempo views group traces by service name. (`OTEL_PROJECT_NAME` / `project_name` sets the Phoenix project, not the service name.)

**"OTLP export hangs"**

- Grafana Cloud's OTLP endpoint requires TLS and Basic Auth. Double-check the base64 encoding of the `Authorization` header, then turn on `HERMES_OTEL_DEBUG=true`: `debug.log` shows the status code of every failed batch.
