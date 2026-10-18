---
sidebar_position: 11
title: "Aspire Dashboard"
description: "Send Hermes traces, metrics and logs to the standalone .NET Aspire Dashboard — one container, no account, a GenAI visualizer, and a read API the plugin dashboard uses for traces and logs."
---

# .NET Aspire Dashboard

The standalone [Aspire Dashboard](https://aspire.dev/dashboard/standalone/) (MIT) is one container that takes OTLP for **all three signals** and shows them within seconds: no account, no database to run, a GenAI visualizer for chat-completion spans, and since 13.6 a read API (`/api/telemetry/*`) that answers OTLP JSON. It is the quickest way to look at a Hermes turn, and the plugin's dashboard adapter reads traces and logs back from it.

**Signals:** traces + metrics + logs. **Deployment:** local (single container) · any container host. **Cost:** open source, no account.

:::tip Verified live
One real `hermes` turn through `type: aspire` into the bundled compose stack (13.6.1, 2026-10-11) stored one trace of 7 spans and 28 log records, the resource reported `hasMetrics: true`, and the dashboard-verify harness read traces and logs back through the adapter. See [Verified behavior](#verified-behavior).
:::

## Quick start

```bash
docker compose -f docker-compose/aspire/docker-compose.yaml up -d
```

Then declare the backend:

```yaml
backends:
  - type: aspire
    endpoint: http://localhost:4418        # OTLP/HTTP (18890 in the container)
    query_port: 18888                      # dashboard read path: the UI port
capture_logs: true
```

The startup banner reads `✓ Aspire Dashboard at http://localhost:4418/v1/traces`. Open http://localhost:18888: Traces, Structured logs and Metrics, and on an `api.*` span the GenAI visualizer shows the prompt and the response the plugin captured.

For a dashboard elsewhere, point `endpoint` at its OTLP/HTTP port (18890 by default) and `query_port` at its UI port.

## Why a dedicated `type: aspire`

Export also works through the [generic OTLP](/backends/otlp) type. Declaring `type: aspire` instead asks the plugin to:

1. Complete `endpoint` to the traces URL (a base URL gets `/v1/traces`) and derive the metrics and logs URLs from it.
2. Send an OTLP API key as `x-otlp-api-key` when `api_key` / `api_key_env` (or `OTEL_ASPIRE_API_KEY`) resolves, which is what `DASHBOARD__OTLP__AUTHMODE=ApiKey` wants; nothing in anonymous mode.
3. Enable all three signals by default; the dashboard stores Gauge, Sum and Histogram points and **rejects exponential histograms**, so the plugin's default explicit-bucket histograms are the right shape.
4. Enable the [dashboard adapter](/dashboard) for the entry.

## Configuration reference

| Field | Meaning |
|---|---|
| `endpoint` | **Required.** The dashboard's OTLP/HTTP base, e.g. `http://localhost:4418` (bundled stack) or `http://<host>:18890`. Also via `OTEL_ASPIRE_ENDPOINT`. |
| `api_key` / `api_key_env` | Optional OTLP API key → `x-otlp-api-key` (fallback `OTEL_ASPIRE_API_KEY`). Only with `DASHBOARD__OTLP__AUTHMODE=ApiKey`. |
| `metrics` / `logs` / `traces` | Per-signal toggles; all three default **on**. |
| `headers` | Extra headers, merged on top of the generated ones. |
| `query_port` | Dashboard read path: the UI port (default `18888`). |
| `ui_url` | Dashboard read path: the UI origin for the "open in" link (default: the query URL). |
| `search_fetch`, `logs_fetch` | Dashboard read path: traces and log records fetched per query before the window and filters are applied (defaults 500 and 5000). |
| `service_name` | Dashboard read path: the service whose resources (one per Hermes process) the API is asked for (default: the plugin's `resource_attributes.service.name`, else `hermes-agent`). |

The adapter's own `api_key` goes in `x-api-key` when `DASHBOARD__API__AUTHMODE=ApiKey`; the same field serves both because the two keys are usually the same deployment-level secret.

## Single backend (env vars)

```bash
export OTEL_ASPIRE_ENDPOINT=http://localhost:4418
export OTEL_PROJECT_NAME=hermes-otel-aspire
```

## What you'll see

The Traces page lists the `agent` turn with its `llm.*`, `api.*` and `tool.*` children; the span details show every attribute in both conventions, and the GenAI visualizer renders `gen_ai.input.messages` / `gen_ai.output.messages` as a conversation. Structured logs carry the trace and span of the lines written inside a span and link to them. Metrics has the plugin's counters and histograms per resource.

## Dashboard adapter

The [Hermes dashboard's OTel tab](/dashboard) reads the dashboard's API on `query_port`:

- **Traces**: `GET /api/telemetry/traces?resource=<instance>&…&limit=<search_fetch>` returns OTLP JSON for every matching trace.
  - An Aspire *resource* is one `service.name` + `service.instance.id`, that is, one Hermes process. The bare service name stops matching once a second process has reported, so the adapter first lists `/api/telemetry/resources` and names every instance of the service (the `resource` parameter is repeatable).
  - The API has no time window and no attribute, status or name filters: on 13.6.1 it honours only `resource`, `limit` and `offset`. The adapter therefore applies the window, every structured filter (`status = ok` and free text included), roots-only and the paging cursor to the rows it fetched.
  - The detail is `GET /api/telemetry/traces/{traceId}`, already in the OTLP shape the frontend reads. Each trace links to the Aspire UI's own `/traces/detail/{traceId}` page.
- **Logs**: `GET /api/telemetry/logs` with the same instance list, `limit=<logs_fetch>`, plus `traceId` and `search` when the filter sets them; span, logger, severity, session and event filters are applied to the rows. Severities arrive as `Information` / `Warning` / `Critical` and are mapped from `severityNumber`.
- **Metrics**: no read route exists, so the source reports `metrics: false`; the Aspire UI shows them.

## Verified behavior

| Check | Result |
|---|---|
| Ingest | `/v1/traces`, `/v1/metrics`, `/v1/logs` → 200 (protobuf and JSON) |
| Spans | 7 in one trace (`agent`, `llm.nvidia/…`, three `api.nvidia/…`, `tool.terminal`, `tool.read_file`); `/api/telemetry/traces` returns them as `resourceSpans` with `totalCount` / `returnedCount` |
| Logs | 28 records, 12 with `traceId` and `spanId`; `?traceId=` → 12, `?search=API call` → 4, `?limit=2&offset=2` pages oldest-first |
| Resources | `/api/telemetry/resources` → `hermes-agent` with `hasLogs`, `hasTraces`, `hasMetrics` all true |
| Read API filters | `resource` (repeatable; a bare service name is ambiguous once two processes have reported), `limit`, `offset`, `traceId`, `search` honoured; `?resource=<unknown>` → 404; no time, severity, scope or attribute parameters recognised |
| UI routes | `/traces/detail/{traceId}` → 200; an unknown route → 404 |
| Dashboard verify harness | fresh four-turn batch: **PASS 48 · SKIP 3** (the skips are the metrics checks the read API cannot serve); per-trace log lines and every filter match the live store |
| Resident memory | about 75 MB |

## Caveats

- **Persistence**: the default `ASPIRE_DASHBOARD_PERSISTENCE_MODE=None` deletes the SQLite database on a clean shutdown; the bundled stack sets `Resume` with a volume on the `app` user's home (a volume on a fresh path is root-owned and the dashboard cannot write it).
- **Exponential histograms are rejected**; keep `metrics_histogram: explicit`.
- The read API has no metrics route and no server-side filters beyond `resource` / `limit` / `offset` / `traceId` / `search`, so large stores make the adapter fetch more than it shows (`search_fetch`, `logs_fetch`).
- `ASPIRE_DASHBOARD_*` is the documented variable prefix; `DOTNET_DASHBOARD_*` still works as an undocumented fallback.
- Limits: 100 000 traces and logs, 50 000 metric points per dimension, 128 attributes per item (`DASHBOARD__TELEMETRYLIMITS__*`); upstream calls the dashboard a development tool, not a durable telemetry backend.

## Troubleshooting

- **`aspire requires endpoint`** — no `endpoint:` and no `OTEL_ASPIRE_ENDPOINT`.
- **Container restarts with `Access to the path … is denied`** — the data directory is not writable by the image's `app` user; mount the volume on `/home/app` as the bundled file does.
- **Dashboard shows nothing after a restart** — persistence mode `None`; set `Resume` with a data directory.
- **Metrics tab says not served** — expected: the read API has no metrics route; use the Aspire UI.
