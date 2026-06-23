---
sidebar_position: 11
title: "Honeycomb"
description: "Honeycomb — cloud observability backend with a generous free tier, OTLP/HTTP-native for traces + metrics + logs."
---

# Honeycomb

[Honeycomb](https://www.honeycomb.io/) is a cloud observability platform built for high-cardinality, event-based debugging. It's OTLP/HTTP-native, so hermes-otel exports straight to it with no collector in between.

**Signals:** traces + metrics + logs. **Deployment:** cloud (US or EU). **Cost:** generous free tier; paid plans for higher volume and the Query Data API.

:::tip Verified live
This backend was tested end-to-end against a real Honeycomb free-tier account (US, a modern "Environments" key): a real `hermes` turn fanned spans out to Honeycomb alongside Phoenix, the OTLP export returned success, and Honeycomb auto-created the datasets. See [Verified behavior](#verified-behavior).
:::

## Quick start

Put your Honeycomb ingest key in an env var:

```bash
export HONEYCOMB_API_KEY="hcaik_..."
```

Then declare the backend in `config.yaml`:

```yaml
backends:
  - type: honeycomb
    region: us                 # us (default) or eu
    api_key_env: HONEYCOMB_API_KEY
```

That's it. The plugin fills in the ingest endpoint and the `x-honeycomb-team` auth header, and derives the per-signal paths automatically. You'll see `✓ Honeycomb at https://api.honeycomb.io/v1/traces` in the startup logs.

Open the Honeycomb UI and your data lands in a dataset named after the agent's `service.name` (see [Datasets](#datasets)).

## Why a dedicated `type: honeycomb`

Trace export also works through the [generic OTLP](/backends/otlp) type if you hand-write the endpoint and `x-honeycomb-team` header. Declaring `type: honeycomb` instead asks the plugin to:

1. Read `api_key:` (or `api_key_env:` → env var → `HONEYCOMB_API_KEY` / `OTEL_HONEYCOMB_API_KEY`).
2. Set the `x-honeycomb-team` request header automatically.
3. Default the endpoint from `region` (US or EU) — no need to remember the URL.
4. Enable all three signals (`supports_metrics=True`, `supports_logs=True`).
5. Display `✓ Honeycomb at <endpoint>` in startup logs.

Keep the key out of `config.yaml` — use `api_key_env` and set the variable in your shell.

## Configuration reference

| Field | Meaning |
|---|---|
| `region` | `us` (default) → `https://api.honeycomb.io`, `eu` → `https://api.eu1.honeycomb.io`. Ignored if `endpoint` is set. |
| `api_key` / `api_key_env` | Honeycomb ingest key → `x-honeycomb-team` header. Prefer `api_key_env`. Falls back to `HONEYCOMB_API_KEY` / `OTEL_HONEYCOMB_API_KEY`. |
| `dataset` | Optional → `x-honeycomb-dataset` header. Only honored by Classic keys — see [Datasets](#datasets). |
| `endpoint` | Optional full `/v1/traces` URL; overrides `region`. Also settable via `OTEL_HONEYCOMB_ENDPOINT`. |
| `metrics` / `logs` / `traces` | Per-signal toggles. All default **on** for Honeycomb. |
| `headers` | Extra headers, merged on top of the generated ones. |

The plugin uses **OTLP/HTTP** (not gRPC). The metrics and logs endpoints are derived from the traces endpoint by rewriting the `/v1/traces` suffix to `/v1/metrics` and `/v1/logs`, which matches Honeycomb's per-signal path scheme.

## Single backend (env vars)

Without a `config.yaml` `backends:` list, the env-var flow picks up Honeycomb when `HONEYCOMB_API_KEY` is set (US endpoint by default):

```bash
export HONEYCOMB_API_KEY="hcaik_..."
# Optional — override the URL (e.g. the EU host):
# export OTEL_HONEYCOMB_ENDPOINT="https://api.eu1.honeycomb.io/v1/traces"
export OTEL_PROJECT_NAME=hermes-otel-honeycomb
```

## Datasets

Honeycomb routes data into *datasets*, and the rules depend on your key type.

**Modern "Environments" keys (what most accounts use today):**

- **Traces** route by the `service.name` resource attribute → a dataset of that name is auto-created. The `x-honeycomb-dataset` header is **ignored**.
- **Metrics** route to the environment's default metrics dataset (named `Metrics`). The header is **ignored** here too.
- **Logs** are ingested as events.
- ⇒ With a modern key, **`dataset` has no observable effect** — leave it unset. Control your trace dataset name via `service.name` (the plugin sets it from the OTel resource / project name, e.g. `OTEL_PROJECT_NAME`).

**Honeycomb Classic keys (legacy 32-char):**

- `x-honeycomb-dataset` is honored and **required for every signal**. Set `dataset` so data isn't rejected or mis-routed.

The plugin sends the same merged header set to the trace, metric, and log exporters, so a `dataset` you set is applied to all three — correct for Classic keys, a harmless no-op for modern keys.

## Verified behavior

Confirmed against a real Honeycomb free-tier account (US region, modern Environments key) by running a real `hermes` turn with Honeycomb configured alongside Phoenix:

- **Startup:** `✓ Honeycomb at https://api.honeycomb.io/v1/traces`; logs and metrics fan-out both reported Honeycomb as a target (Phoenix stayed traces-only).
- **Export:** a span pushed through the plugin's resolved exporter returned `SpanExportResult.SUCCESS`; the full `hermes` turn produced no export errors.
- **Ingestion:** Honeycomb auto-created a **trace** dataset named from `service.name` (~16 columns of GenAI / `llm.*` attributes) and a default **`Metrics`** dataset. A `dataset:` set in config did not override either — confirming the header is ignored for modern keys.
- **Not confirmed:** individual column names / span contents — the test key's scope was `events + createDatasets + markers` but not `columns` or `queries`, so the management API couldn't enumerate columns or run queries. Dataset column counts are strong evidence the attributes landed; attribute-level confirmation needs a key with `columns`/`queries` access (or the Honeycomb UI).

## Multi-backend fan-out

Honeycomb runs happily alongside a local backend — each gets its own batch processor and worker thread, so they export in parallel and a slow one can't block the others. A common setup is local Phoenix for the bundled dashboard plus Honeycomb for durable cloud storage:

```yaml
query_backend: phoenix
backends:
  - type: phoenix
    endpoint: http://localhost:6006/v1/traces
  - type: honeycomb
    region: us
    api_key_env: HONEYCOMB_API_KEY

capture_logs: true
```

Traces fan out to both, metrics to both, logs only to Honeycomb (Phoenix doesn't accept OTLP logs — the plugin skips it automatically). See [Multi-backend fan-out](/backends/multi-backend).

## Limitations

- **No bundled-dashboard query support (export-only).** The hermes-otel [dashboard](/backends/multi-backend) queries a backend to render traces (e.g. Phoenix via GraphQL). Honeycomb's [Query Data API](https://api-docs.honeycomb.io/api/query-data) is Enterprise-plan gated, asynchronous (create query → run → poll), oriented toward aggregated results rather than fetching raw spans by trace ID, and bounded to a 7-day window — a poor fit for the dashboard. Point `query_backend` at a local backend and explore Honeycomb data in Honeycomb's own UI.
- **Dataset header ignored on modern keys** — control the trace dataset via `service.name`; see [Datasets](#datasets).

## Troubleshooting

**`Unknown API key` / HTTP 401 at startup**
The key is for the wrong region. US and EU are separate; a US key 401s against `api.eu1.honeycomb.io` and vice versa. Set `region:` to match where your team lives.

**Traces appear but metrics don't show where I expected**
On a modern key, metrics go to the default `Metrics` dataset regardless of `dataset:`. That's expected — see [Datasets](#datasets).

**`✓ Honeycomb at ...` but nothing in the UI**
Check you're looking at the right environment (the key is environment-scoped) and the dataset named after your `service.name`. Ingestion is near-real-time but can lag a few seconds.

## See also

- [Generic OTLP](/backends/otlp) — the no-dedicated-type path Honeycomb also works through.
- [Multi-backend fan-out](/backends/multi-backend) — adding Honeycomb alongside a local backend.
- [OTel logs](/configuration/logs) — the log pipeline that ships logs to Honeycomb.
- [Send Data to Honeycomb with OpenTelemetry](https://docs.honeycomb.io/send-data/opentelemetry) — upstream docs.
