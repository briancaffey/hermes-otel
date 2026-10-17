---
sidebar_position: 12
title: "Gigapipe (qryn)"
description: "Send Hermes traces, metrics and logs to Gigapipe (formerly qryn) — a polyglot ClickHouse store with OTLP in and the Tempo, Prometheus and Loki APIs out on one port, read back by the plugin's LGTM adapters."
---

# Gigapipe (formerly qryn)

[Gigapipe](https://github.com/metrico/gigapipe) (AGPL-3.0; `metrico/qryn` redirects there) is a polyglot observability store on ClickHouse. It ingests OTLP for **all three signals** on one port and serves Grafana's three query APIs, Tempo, Prometheus and Loki, from the same port. For the plugin that makes it an LGTM-shaped target: the same adapters read it, with the handful of places Gigapipe answers differently absorbed by a small subclass.

**Signals:** traces + metrics + logs. **Deployment:** local (Gigapipe + ClickHouse) · Gigapipe Cloud. **Cost:** OSS, no account.

:::tip Verified live
One real `hermes` turn through `type: gigapipe` into the bundled compose stack (Gigapipe v5.5.3, ClickHouse 24.9.1, 2026-10-11) stored one trace of 7 spans, 28 log lines and 27 metric names, and the dashboard-verify harness read every signal back through the adapter. See [Verified behavior](#verified-behavior).
:::

## Quick start

```bash
docker compose -f docker-compose/gigapipe/docker-compose.yaml up -d
```

Then declare the backend:

```yaml
backends:
  - type: gigapipe
    endpoint: http://localhost:3110        # one port for ingest, the three query APIs and the UI
capture_logs: true
```

The startup banner reads `✓ Gigapipe at http://localhost:3110/v1/traces`. Open http://localhost:3110 for the built-in qryn-view, or point Grafana's Tempo, Prometheus and Loki data sources at the same URL.

## Why a dedicated `type: gigapipe`

Export works through the [generic OTLP](/backends/otlp) or `lgtm` type. Declaring `type: gigapipe` instead asks the plugin to:

1. Complete `endpoint` to the traces URL (a base URL gets `/v1/traces`) and derive the metrics and logs URLs from it; send Basic auth from `user` / `password` for deployments with `GIGAPIPE_LOGIN` / `GIGAPIPE_PASSWORD`, nothing otherwise.
2. Enable all three signals, cumulative: Gigapipe **rejects delta points** one by one (`partial_success`), so the SDK default is the right one.
3. Enable the [dashboard adapter](/dashboard): the LGTM adapter with every read API defaulting to the entry's own origin, plus the Gigapipe-specific handling below.

## Configuration reference

| Field | Meaning |
|---|---|
| `endpoint` | **Required.** Gigapipe's origin, e.g. `http://localhost:3110` (bundled stack) or `http://<host>:3100`. Also via `OTEL_GIGAPIPE_ENDPOINT`. |
| `user` / `user_env`, `password` / `password_env` | Optional Basic auth (fallbacks `OTEL_GIGAPIPE_USER` / `OTEL_GIGAPIPE_PASSWORD`). |
| `metrics` / `logs` / `traces` | Per-signal toggles; all three default **on**. |
| `headers` | Extra headers, merged on top of the generated ones. |
| `query_port`, `prometheus_url`, `loki_url` | Dashboard read path: all default to the endpoint's origin; `loki_url: off` disables the Logs tab for the entry. |
| `search_fetch`, `metrics_match`, `loki_selector`, `ui_url` | Dashboard read path, as for `lgtm`. |

No metric-temporality preset.

## Single backend (env vars)

```bash
export OTEL_GIGAPIPE_ENDPOINT=http://localhost:3110
export OTEL_PROJECT_NAME=hermes-otel-gigapipe
```

## What you'll see

Traces land under the Tempo API (TraceQL works, `nestedSetParent < 0` included), metrics under the Prometheus API in Prometheus spelling (`hermes_token_usage_total`, `gen_ai_client_token_usage_sum` / `_count` / `_bucket`, `target_info`), logs under the Loki API as streams whose labels are every log and resource attribute with dots turned into underscores (`hermes_session_id`, `trace_id`, `span_id`, `level`).

## Dashboard adapter

The [Hermes dashboard's OTel tab](/dashboard) reads Gigapipe with the `lgtm` / `tempo` adapter, every sibling URL defaulting to the one port, and four Gigapipe detours (all verified on v5.5.3):

- **Cards come from the trace detail.** `/api/search` with a TraceQL `q` returns one row per trace, but its `| select(...)` answers an empty attribute list, so the adapter fetches `/api/traces/{id}` for each card and takes the model, tokens and session from the root span (or, on a widened kind / tool search, from the first matching span; with a tool filter alone the card is still the trace's root).
- **The detail is pre-1.0 OTLP JSON**: `resourceSpans` holding `instrumentationLibrarySpans`, every attribute a `stringValue`, no `kind`, the resource reduced to `collector=gigapipe` with the service on each span's `serviceName`. It is reshaped into the `batches` / `scopeSpans` the frontend consumes, values coerced back and one batch per service.
- **Counters via `query_range` on the 15 s rollup**: Gigapipe answers a range-vector instant query (`name[3600s]`) with an empty result, so counters are read at step 15 with the five-minute look-behind and turned into per-bucket increases with first-sample counting; gauges take the normal `query_range` aggregate.
- **Label-only Loki streams**: the level is a `level` label (no `severity_number` / `severity_text`), there is no scope (logger) label, and every attribute is a label. The LogQL stages use `trace_id`, `span_id`, `hermes_session_id`, `event_name`, `level=~"WARN|…"` and `|=` text; the logger filter and the loggers list cannot be served and come back empty.
- `status = ok`, free text and raw TraceQL work as on Tempo; roots are a server predicate. No "open in" link unless `ui_url` names a Grafana (the qryn-view UI has no documented deep link).

## Verified behavior

| Check | Result |
|---|---|
| Ingest | `/v1/traces` 200, `/v1/metrics` 200, `/v1/logs` 204 (protobuf) |
| Spans | one trace of 7 (`agent` root, `llm.nvidia/…`, three `api.nvidia/…`, `tool.terminal`, `tool.read_file`); `/api/traces/{id}` has `parentSpanId` on the children, string-typed attributes, no `kind` |
| TraceQL | `{}`, `{ nestedSetParent < 0 }`, `{ .tool.name = "terminal" }`, `{ status = error }`, `{ duration > 3s }` and conjunctions work; `select()` returns no attributes; without `q`, `/api/search` lists one row per span |
| Metrics | 27 names in Prometheus spelling; `query_range` at step 15 / 60 returns forward-filled samples; `name[300s]` / `name[1h]` instant queries return an empty matrix |
| Logs | 28 lines in label-only streams (`level`, `trace_id`, `span_id`, `hermes_session_id`, …); `trace_id="…"` 12, `level="WARN"` 1, `|= "API call"` 3; `severity_number >= 13` and `scope_name="cli"` 0 (no such labels) |
| Dashboard verify harness | fresh four-turn batch: **PASS 46 · SKIP 5** (the skips: no UI deep link ×4, no loggers list); token, model-call and tool-call totals and per-trace log lines equal to the live store |
| Resident memory | Gigapipe about 16 MB, ClickHouse about 300 MB |

## Caveats

- **AGPL-3.0.**
- **PromQL is served from the 15 s rollup** (`METRICS_15S_ENABLED`), raw samples live 7 days (`SAMPLES_DAYS`); the open bug [#1027](https://github.com/metrico/gigapipe/issues/1027) makes a coarse, unaligned `query_range` come back empty, which the adapter sidesteps by reading at step 15 and bucketing itself.
- **Delta metrics are rejected** per point; keep the cumulative default.
- **No logger in the Logs tab**: Gigapipe keeps no scope label.
- TraceQL's unary `!()` is unsupported upstream ([#988](https://github.com/metrico/gigapipe/issues/988)).
- Trace ids come back upper-case from a tag search and lower-case from a TraceQL one; the adapter lower-cases and pads them.

## Troubleshooting

- **`gigapipe requires endpoint`** — no `endpoint:` and no `OTEL_GIGAPIPE_ENDPOINT`.
- **Metrics tab shows zero for a counter** — the five-minute look-behind treats a series seen before the window as running; widen the window, or wait for the next sample.
- **Cards without model or tokens** — the detail fetch failed (check the backend URL); the search row itself carries none.
