---
sidebar_position: 13
title: "Elastic"
description: "Elastic observability for Hermes agent runs — Elastic Cloud managed OTLP (mOTLP) or a self-hosted EDOT Collector, via OTLP/HTTP."
---

# Elastic

[Elastic](https://www.elastic.co/observability) ingests OpenTelemetry traces, metrics and logs into Elasticsearch data streams. hermes-otel speaks plain OTLP/HTTP to it, so both deployment shapes work with one `type: elastic` entry:

- **Elastic Cloud (managed OTLP, "mOTLP")** — one `https://<deployment>.ingest.<region>.<csp>.elastic.cloud` endpoint per Elastic Cloud project or deployment, authenticated with an API key: `Authorization: ApiKey <key>` (not `Bearer`). The classic APM Server host (`https://<hash>.apm.<region>.<csp>.elastic-cloud.com`) accepts the same OTLP/HTTP requests and key scheme, so either URL works here.
- **Self-hosted EDOT Collector** — your own [Elastic Distribution of OpenTelemetry](https://github.com/elastic/elastic-agent) collector on the network; typically no auth at all.

**Signals:** traces + metrics + logs. **Cost:** depends on your Elastic license/plan.

## Quick start

### Elastic Cloud (mOTLP)

Copy the managed OTLP endpoint from Kibana (**Add data → OpenTelemetry**) and create an API key (**Stack Management → API Keys**, or `POST /_security/api_key` with `name: "hermes-otel"`). The key must carry the `apm` application's `event:write` privilege — the one Kibana's OpenTelemetry onboarding flow creates — or the endpoint answers `401` even though the scheme is right. Then:

```yaml
backends:
  - type: elastic
    endpoint: https://<deployment>.ingest.<region>.<csp>.elastic.cloud
    api_key_env: OTEL_ELASTIC_API_KEY
```

```bash
export OTEL_ELASTIC_API_KEY="<base64 api key>"
```

The plugin appends `/v1/traces`, `/v1/metrics`, `/v1/logs` to the endpoint as needed and sends `Authorization: ApiKey <key>` on every export. Elastic Cloud has not been exercised live by the maintainers yet (the self-hosted path below has); if the managed endpoint rejects something, say so in [#318](https://github.com/briancaffey/hermes-otel/issues/318).

### Self-hosted EDOT Collector

```yaml
backends:
  - type: elastic
    endpoint: http://localhost:4318
```

No API key is needed for a collector on a trusted network: when no key resolves, the `Authorization` header is omitted entirely (set one anyway if your collector has auth enabled — the header shape is the same).

A complete local stack (Elasticsearch + Kibana + EDOT Collector) ships in [`docker-compose/elastic/`](https://github.com/briancaffey/hermes-otel/tree/main/docker-compose/elastic), ports bound to 127.0.0.1 only (Elasticsearch 19201, Kibana 15602, collector 14319, so it runs alongside the other stacks):

```bash
docker compose -f docker-compose/elastic/docker-compose.yml up -d
uv run --extra dev python scripts/verify_elastic.py   # end-to-end smoke: export + verify in ES
./scripts/import_elastic_dashboard.sh                 # optional: hermes-otel dashboard in Kibana
```

Give the single node a minute after `up -d` before the first export: the first bulk request has to create the index templates and data streams, and on a busy laptop that can exceed the collector's request timeout, in which case the collector logs `bulk indexer flush error: context deadline exceeded` and drops the batch. The bundled `otel.yaml` sets a 90 s request timeout with retries and a queue for exactly this, and the compose file turns the disk watermark off so a Docker VM above 90 % full does not leave the cluster red with unassigned shards.

The bundled dashboard (`docker-compose/elastic/dashboards.ndjson`) ships five panels — top span names, spans by operation, token usage by token type (sum of `hermes.token.usage`), tool-call duration by tool, and messages per model (sum of `hermes.model.usage`) — rendered with real session data in [`dashboard.png`](https://github.com/briancaffey/hermes-otel/blob/main/docker-compose/elastic/dashboard.png). Kibana listens on `http://127.0.0.1:15602` (security disabled).

### Importing into a security-enabled Kibana

On a Kibana with security enabled (Elastic Cloud, or a local stack started with `xpack.security.enabled=true`), the unauthenticated import request is rejected with HTTP 401. Pass credentials via environment variables — they are never echoed or logged:

```bash
# API key (recommended): create one in Stack Management → API Keys, or
# POST /_security/api_key with {"name": "hermes-otel-dashboard"}
export KIBANA_API_KEY="<base64 api key>"
./scripts/import_elastic_dashboard.sh https://<your-kibana>:5601

# or basic auth
export KIBANA_USERNAME=elastic KIBANA_PASSWORD="<password>"
./scripts/import_elastic_dashboard.sh https://<your-kibana>:5601
```

`KIBANA_API_KEY` takes precedence when both are set; with neither set the request is sent unauthenticated, which keeps the no-login compose path working.

## Why a dedicated `type: elastic`

Trace export also works through the [generic OTLP](/backends/otlp) type with a hand-written endpoint and header. Declaring `type: elastic` instead asks the plugin to:

1. Read `api_key:` (or `api_key_env:` → env var → `OTEL_ELASTIC_API_KEY` / `ELASTIC_API_KEY`).
2. Send the key as `Authorization: ApiKey <key>` — the scheme Elastic's mOTLP endpoint requires — and omit the header entirely when there is no key.
3. Default `metrics_temporality: delta`: Elasticsearch does not handle cumulative histograms ([Elastic OTel limitations](https://www.elastic.co/docs/reference/opentelemetry/compatibility/limitations#histogram-and-counter-temporality)).
4. Route optional `dataset` / `namespace` fields to the `data_stream.dataset` / `data_stream.namespace` Resource attributes (see [Data streams](#data-streams)).

## Configuration reference

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** mOTLP URL or EDOT Collector OTLP/HTTP base. Also via `OTEL_ELASTIC_ENDPOINT`. A base URL gets the per-signal `/v1/*` suffixes automatically; a `/v1/metrics` or `/v1/logs` URL is normalised to the base first |
| `api_key` | string | API key (discouraged inline — use `api_key_env`). Sent as `Authorization: ApiKey <key>` |
| `api_key_env` | string | Env var holding the key (`OTEL_ELASTIC_API_KEY` / `ELASTIC_API_KEY` fallbacks) |
| `dataset` | string | Optional `data_stream.dataset` Resource attribute (the collector appends `.otel`) |
| `namespace` | string | Optional `data_stream.namespace` Resource attribute |
| `metrics_temporality` | string | Defaults to `delta` for this type; `cumulative` overrides |

The [Hermes dashboard's OTel tab](/dashboard) has no query adapter for `elastic` (like Honeycomb and Weave), so the entry exports only; use Kibana to read the data back.

## Data streams

Elasticsearch stores OTLP data in data streams named `<type>-<dataset>-<namespace>`, keyed by the `data_stream.*` Resource attributes. In the collector's `otel` mapping mode the dataset defaults to `generic.otel` and the namespace to `default`, so without any routing the plugin's data lands in `traces-generic.otel-default`, `metrics-generic.otel-default` and `logs-generic.otel-default`. Set the fields explicitly when you want Hermes data in streams of its own:

```yaml
backends:
  - type: elastic
    endpoint: https://<hash>.apm.eu-west-1.gcp.elastic-cloud.com:443
    api_key_env: OTEL_ELASTIC_API_KEY
    dataset: hermes_otel
    namespace: agents
```

The collector appends `.otel` to the dataset it was given, so spans land in `traces-hermes_otel.otel-agents`, metrics in `metrics-hermes_otel.otel-agents` and logs in `logs-hermes_otel.otel-agents` (observed on EDOT 9.5.5; index documents carry `data_stream.dataset: hermes_otel.otel`). Nothing is routed outside your Elastic deployment — the attributes only name data streams inside it.

`dataset` and `namespace` must be valid data-stream components: lowercase letters, digits, `_` and `.`, up to 100 characters, and no `-` (data-stream names are split on `-`; the collector would silently rewrite it to `_`). An invalid value is reported at startup as `backend 'elastic' skipped: elastic dataset '…' is not a valid data-stream component` and that entry is left out, the same fail-open rule every backend follows; the agent keeps running.

Both attributes are set on the plugin's single shared OTel Resource, so in a [multi-backend](/backends/multi-backend) configuration every other backend sees them too (harmless), and two `elastic` entries with different `dataset` / `namespace` values conflict at startup.

## Metrics temporality

Elasticsearch histograms do not support cumulative temporality; cumulative counters are also converted lossily. The plugin therefore defaults this backend to `metrics_temporality: delta`, matching the per-backend `metrics_temporality` knob (see the SigNoz/Datadog discussion in [#226](https://github.com/briancaffey/hermes-otel/issues/226)). You can still override it per entry with `metrics_temporality: cumulative` if your pipeline handles conversion downstream.

## Logs

The elastic backend exports logs when `capture_logs: true` is set (it is not on by default). Log records land in the `logs-<dataset>-<namespace>` data stream, one stream per signal; verified with a real `hermes -z` session against the bundled compose stack — spans, delta metrics and log records (with readable `body.text`) each in their own stream. One caveat: the OTel SDK's `LoggingHandler` emits log records asynchronously in batches, so the last few records of a short-lived session may not flush before shutdown; long-running sessions lose nothing.

## Troubleshooting

- **`401 Unauthorized`** — the key is wrong, expired, or sent with the wrong scheme. The plugin always uses `ApiKey`; check the key in Kibana (**Stack Management → API Keys**).
- **`elastic requires endpoint`** — no `endpoint:` and no `OTEL_ELASTIC_ENDPOINT` set. Unlike SaaS backends there is no default host to fall back on.
- **Metrics missing / wrong bucket counts** — temporality mismatch; leave the `delta` preset in place.
- **Nothing lands although the plugin logs `SUCCESS`** — the collector accepted the batch and then failed to index it; check `docker logs hermes-otel-edot` for `bulk indexer flush error` and `curl :19201/_cluster/health` for a `red` status (unassigned shards after a disk-watermark trip or a cold start).
