---
sidebar_position: 7
title: "Zipkin"
description: "Send Hermes traces to Zipkin through the zipkin-otel image — the original distributed-tracing UI, one container, Apache-2.0, read back by the dashboard adapter."
---

# Zipkin

[Zipkin](https://zipkin.io) (Apache-2.0) is the original distributed-tracing UI. The stock `openzipkin/zipkin` image has **no OTLP endpoint**: OTLP ingest comes from the [`zipkin-otel`](https://github.com/openzipkin-contrib/zipkin-otel) contrib module, published as its own image on top of the full server. With that image, `POST /v1/traces` lives on the query port (9411) next to the UI and the v2 API.

**Signals:** traces only. **Deployment:** local (single container). **Cost:** OSS, no account.

:::tip Verified live
One real `hermes` turn through `type: zipkin` into the bundled compose stack (`ghcr.io/openzipkin-contrib/zipkin-otel:0.3.0`, Zipkin 3.6.0, 2026-10-11) stored one trace of 7 spans: the `agent` root, `llm.*`, three `api.*`, `tool.terminal` and `tool.read_file`. The dashboard adapter read it back with every filter. See [Verified behavior](#verified-behavior).
:::

## Quick start

```bash
docker compose -f docker-compose/zipkin/docker-compose.yaml up -d
```

Then declare the backend (the origin is enough; the type appends `/v1/traces`):

```yaml
backends:
  - type: zipkin
    endpoint: http://localhost:9411
```

The startup banner reads `✓ Zipkin at http://localhost:9411/v1/traces (traces only)`. Open http://localhost:9411/zipkin, widen the lookback from the default 15 minutes if needed, and search for service `hermes-agent`.

For a Zipkin elsewhere, point `endpoint` at its query port, and make sure it runs the `zipkin-otel` module (`curl -s http://<host>:9411/health` lists `OpenTelemetryHttpCollector{}` when it does).

## Why a dedicated `type: zipkin`

Export also works through the [generic OTLP](/backends/otlp) type with `metrics: false` and `logs: false`. Declaring `type: zipkin` instead asks the plugin to:

1. Complete `endpoint` to the traces URL (a bare origin gets `/v1/traces`; a `/v1/metrics` or `/v1/logs` URL is rewritten).
2. Keep metrics and logs **off** by default: `/v1/metrics` answers 404, and `/v1/logs` answers 202 but keeps a record only as an annotation on a span it can name through an `event.name` attribute, which the plugin's records do not carry.
3. Display `✓ Zipkin at <endpoint> (traces only)` in startup logs.
4. Enable the [dashboard adapter](/dashboard) for the entry.

## Configuration reference

| Field | Meaning |
|---|---|
| `endpoint` | **Required.** The Zipkin origin, e.g. `http://localhost:9411`. Also via `OTEL_ZIPKIN_ENDPOINT`. |
| `query_port` | Dashboard read path: the port serving API v2 and the UI (default: the endpoint's port, else `9411`). |
| `service_name` | Dashboard read path: the service every search names (default: the plugin's `resource_attributes.service.name`, else `hermes-agent`). |
| `api_key` / `api_key_env` | Dashboard read path: a bearer token for an authenticating proxy; Zipkin itself has no auth. |
| `metrics` / `logs` / `traces` | Per-signal toggles. Metrics and logs default **off**; `metrics: true` only makes sense with a collector in front. |
| `headers` | Extra headers on the OTLP requests, for a proxy. |

No metric-temporality preset (no metrics), and the `agent` root keeps its token roll-up (Zipkin totals nothing itself).

## Single backend (env vars)

```bash
export OTEL_ZIPKIN_ENDPOINT=http://localhost:9411
export OTEL_PROJECT_NAME=hermes-otel-zipkin
```

## What you'll see

Zipkin shows the turn as a standard trace tree. Nothing is LLM-specific: token counts, model names and message content are tags on each span, and the plugin emits both the OpenInference (`llm.*`) and OTel GenAI (`gen_ai.*`) spellings, so look for `gen_ai.usage.input_tokens` or `llm.token_count.prompt`.

What Zipkin changes on the way in (from the module's `SpanTranslator`, confirmed on the stored trace):

- **Every attribute is a string tag.** `"7589"`, `"true"`, `"1442.3"`; arrays are comma-joined without brackets, kvlists are JSON.
- The OTLP status becomes the `otel.status_code` tag (`OK` / `ERROR`); on ERROR an `error` tag carries the message.
- Span events become annotations whose value is the event name, or `"name":{...}` when the event had attributes. Span links are dropped.
- `service.name` becomes `localEndpoint.serviceName`; a span without it is invisible to search. INTERNAL spans have no kind.

## Dashboard adapter

The [Hermes dashboard's OTel tab](/dashboard) reads Zipkin through API v2 on the same port: `/api/v2/traces` with `serviceName`, `annotationQuery` (`key=value and key2=value2`) for the model, session, tool and error-status filters, `spanName` (exact) and `minDuration`, inside an `endTs` + `lookback` window; `/api/v2/trace/{id}` for the detail. Numeric and boolean tag values are coerced back (except on id-like keys), annotations become span events again, and roots are the spans without a `parentId`. Because `annotationQuery` matches any span of a trace, a roots-only search re-checks the filter on the root, like the Jaeger adapter. `status = ok` and free text are not expressible (no negative or body search in API v2). Each trace links to Zipkin's own `/zipkin/traces/{id}` page.

## Verified behavior

Read back from the stack after one turn (`project_name: zipkin-364`):

```bash
curl -s 'http://localhost:9411/api/v2/traces?serviceName=hermes-agent&limit=5' \
  | python3 -c "import json,sys; [print(len(t),'spans', sorted(s['name'] for s in t)) for t in json.load(sys.stdin)]"
```

| Check | Result |
|---|---|
| Spans | 7 in one trace: `agent` (no `parentId`), `llm.nvidia/…`, three `api.nvidia/…`, `tool.terminal`, `tool.read_file` |
| `/v1/metrics` | 404 |
| `/v1/logs` | 202, nothing stored |
| Tags | all strings; `gen_ai.usage.input_tokens: "25744"` on the root, `hermes.session_id` present |
| Query grammar | `annotationQuery=tool.name=terminal and openinference.span.kind=TOOL` matched; a model value with `/` matched; `spanName=tool.` (a prefix) matched nothing; `minDuration` and `endTs`/`lookback` bound the results as documented |
| Resident memory | about 300 MB |

The plugin's debug log showed `export zipkin-local: N span(s) -> SUCCESS` for every batch.

## Caveats

- **The image matters.** `openzipkin/zipkin` and `openzipkin/zipkin-slim` have no `/v1/traces`; use `ghcr.io/openzipkin-contrib/zipkin-otel`. The module is marked experimental upstream and requires 128-bit trace ids (OTLP always sends them).
- In-memory storage by default (`STORAGE_TYPE=mem`, capped at 500 000 spans): everything is gone when the container stops. Zipkin also supports Elasticsearch, Cassandra and MySQL stores.
- The UI searches the last 15 minutes by default; `/api/v2/traces` defaults to 24 hours (`QUERY_LOOKBACK`).
- A value containing ` and ` cannot be expressed in `annotationQuery` (no quoting).

## Troubleshooting

- **`zipkin requires endpoint`** — no `endpoint:` and no `OTEL_ZIPKIN_ENDPOINT`; there is no default host.
- **Exports succeed but nothing appears** — the server is the stock image (check `/health` for `OpenTelemetryHttpCollector{}`), or the UI lookback is too short.
- **`404` on `/v1/traces`** — stock Zipkin image, or a reverse proxy that only forwards `/zipkin` and `/api`.
