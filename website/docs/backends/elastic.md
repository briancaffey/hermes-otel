---
sidebar_position: 13
title: "Elastic"
description: "Elastic observability for Hermes agent runs — Elastic Cloud managed OTLP (mOTLP) or a self-hosted EDOT Collector, via OTLP/HTTP."
---

# Elastic

[Elastic](https://www.elastic.co/observability) ingests OpenTelemetry traces, metrics and logs into Elasticsearch data streams. hermes-otel speaks plain OTLP/HTTP to it, so both deployment shapes work with one `type: elastic` entry:

- **Elastic Cloud (managed OTLP, "mOTLP")** — one endpoint per Elastic Cloud project, authenticated with an API key: `Authorization: ApiKey <key>` (not `Bearer`).
- **Self-hosted EDOT Collector** — your own [Elastic Distribution of OpenTelemetry](https://github.com/elastic/elastic-agent) collector on the network; typically no auth at all.

**Signals:** traces + metrics + logs. **Cost:** depends on your Elastic license/plan.

## Quick start

### Elastic Cloud (mOTLP)

Create an API key in Kibana (**Stack Management → API Keys**, or `POST /_security/api_key` with `name: "hermes-otel"`), then:

```yaml
backends:
  - type: elastic
    endpoint: https://<hash>.apm.<region>.gcp.elastic-cloud.com:443
    api_key_env: OTEL_ELASTIC_API_KEY
```

```bash
export OTEL_ELASTIC_API_KEY="<base64 api key>"
```

The plugin appends `/v1/traces`, `/v1/metrics`, `/v1/logs` to the endpoint as needed and sends `Authorization: ApiKey <key>` on every export.

### Self-hosted EDOT Collector

```yaml
backends:
  - type: elastic
    endpoint: http://localhost:4318
```

No API key is needed for a collector on a trusted network: when no key resolves, the `Authorization` header is omitted entirely (set one anyway if your collector has auth enabled — the header shape is the same).

## Why a dedicated `type: elastic`

Trace export also works through the [generic OTLP](/backends/otlp) type with a hand-written endpoint and header. Declaring `type: elastic` instead asks the plugin to:

1. Read `api_key:` (or `api_key_env:` → env var → `OTEL_ELASTIC_API_KEY` / `ELASTIC_API_KEY`).
2. Send the key as `Authorization: ApiKey <key>` — the scheme Elastic's mOTLP endpoint requires — and omit the header entirely when there is no key.
3. Default `metrics_temporality: delta`: Elasticsearch does not handle cumulative histograms ([Elastic OTel limitations](https://www.elastic.co/docs/reference/opentelemetry/compatibility/limitations#histogram-and-counter-temporality)).
4. Route optional `dataset` / `namespace` fields to the `data_stream.dataset` / `data_stream.namespace` Resource attributes (see [Data streams](#data-streams)).

## Configuration reference

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** mOTLP URL or EDOT Collector OTLP/HTTP base. Also via `OTEL_ELASTIC_ENDPOINT`. A base URL gets the per-signal `/v1/*` suffixes automatically |
| `api_key` | string | API key (discouraged inline — use `api_key_env`). Sent as `Authorization: ApiKey <key>` |
| `api_key_env` | string | Env var holding the key (`OTEL_ELASTIC_API_KEY` / `ELASTIC_API_KEY` fallbacks) |
| `dataset` | string | Optional `data_stream.dataset` Resource attribute |
| `namespace` | string | Optional `data_stream.namespace` Resource attribute |

## Data streams

Elasticsearch stores OTLP data in data streams keyed by `data_stream.*` Resource attributes. By default the dataset is derived per signal (`traces`, `metrics.*`, `logs.*`) and the namespace defaults to `default`. Set them explicitly when you want everything in one namespace or a custom dataset prefix:

```yaml
backends:
  - type: elastic
    endpoint: https://<hash>.apm.eu-west-1.gcp.elastic-cloud.com:443
    api_key_env: OTEL_ELASTIC_API_KEY
    dataset: hermes-otel
    namespace: agents
```

Spans land in `traces-hermes-otel-agents`, metrics in `metrics-hermes-otel-agents`, logs in `logs-hermes-otel-agents`. Nothing is routed outside your Elastic deployment — the attributes only name data streams inside it.

## Metrics temporality

Elasticsearch histograms do not support cumulative temporality; cumulative counters are also converted lossily. The plugin therefore defaults this backend to `metrics_temporality: delta`, matching the per-backend `metrics_temporality` knob (see the SigNoz/Datadog discussion in [#226](https://github.com/briancaffey/hermes-otel/issues/226)). You can still override it per entry with `metrics_temporality: cumulative` if your pipeline handles conversion downstream.

## Troubleshooting

- **`401 Unauthorized`** — the key is wrong, expired, or sent with the wrong scheme. The plugin always uses `ApiKey`; check the key in Kibana (**Stack Management → API Keys**).
- **`elastic requires endpoint`** — no `endpoint:` and no `OTEL_ELASTIC_ENDPOINT` set. Unlike SaaS backends there is no default host to fall back on.
- **Metrics missing / wrong bucket counts** — temporality mismatch; leave the `delta` preset in place.
