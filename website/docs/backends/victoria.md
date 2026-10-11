---
sidebar_position: 10
title: "VictoriaMetrics family"
description: "Send Hermes traces, metrics and logs to VictoriaTraces, VictoriaMetrics and VictoriaLogs — three single-binary open-source stores, one OTLP route each, read back by the dashboard adapter through the Jaeger, Prometheus and LogsQL APIs."
---

# VictoriaTraces + VictoriaMetrics + VictoriaLogs

The [VictoriaMetrics](https://docs.victoriametrics.com) family (all Apache-2.0) covers the three signals with three single-binary stores that each ingest OTLP/HTTP directly: [VictoriaTraces](https://docs.victoriametrics.com/victoriatraces/) for traces, [VictoriaMetrics](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/) for metrics and [VictoriaLogs](https://docs.victoriametrics.com/victorialogs/) for logs. One `type: victoria` entry drives all three, and the dashboard adapter reads them back through VictoriaTraces' Jaeger-compatible API, VictoriaMetrics' Prometheus API and VictoriaLogs' LogsQL.

**Signals:** traces + metrics + logs (each only when its store is named). **Deployment:** local (three containers) · Helm (one chart per store) · VictoriaMetrics Cloud. **Cost:** OSS, no account.

:::tip Verified live
One real `hermes` turn through `type: victoria` into the bundled compose stack (VictoriaTraces v0.12.0, VictoriaLogs v1.53.0, VictoriaMetrics v1.153.0, 2026-10-11) stored one trace of 7 spans, 28 log records and 27 metric names, and the dashboard-verify harness read every signal back through the adapter. See [Verified behavior](#verified-behavior).
:::

## Quick start

```bash
docker compose -f docker-compose/victoria/docker-compose.yaml up -d
```

Then declare the backend. Each field takes the store's origin (the type appends the right ingest path), or the full URL:

```yaml
backends:
  - type: victoria
    endpoint: http://localhost:10428          # VictoriaTraces → /insert/opentelemetry/v1/traces
    metrics_endpoint: http://localhost:8428   # VictoriaMetrics → /opentelemetry/v1/metrics
    logs_endpoint: http://localhost:9428      # VictoriaLogs → /insert/opentelemetry/v1/logs
capture_logs: true
```

The startup banner reads `✓ Victoria at http://localhost:10428/insert/opentelemetry/v1/traces`. The UIs are `vmui` on each store: traces at http://localhost:10428/select/vmui (Trace Explorer), logs at http://localhost:9428/select/vmui/, metrics at http://localhost:8428/vmui. Metrics show up about 30 s after export (`-search.latencyOffset`).

## Why a dedicated `type: victoria`

Three stores mean three hosts and three non-standard paths. The generic OTLP type derives `/v1/metrics` and `/v1/logs` from the traces URL on one host, which cannot reach VictoriaMetrics or VictoriaLogs. `type: victoria` asks the plugin to:

1. Resolve `endpoint`, `metrics_endpoint` and `logs_endpoint` to the three stores' OTLP routes (this type introduced per-signal endpoints on the exporter side; the other types still derive).
2. Switch a signal off when its store is not named, since there is nothing to derive it on; `metrics: true` without `metrics_endpoint` is a configuration error that skips the entry with a hint.
3. Send `VL-Stream-Fields: service.name` on logs, so VictoriaLogs keys its streams on the service rather than on every resource attribute (`service.instance.id` changes with each Hermes process); `headers` overrides it.
4. Send Basic auth from `user` / `password` on every store when they resolve (for `-httpAuth.*` deployments), nothing otherwise.
5. Enable the [dashboard adapter](/dashboard) for the entry.

## Configuration reference

| Field | Meaning |
|---|---|
| `endpoint` | **Required.** VictoriaTraces origin or ingest URL. Also via `OTEL_VICTORIA_ENDPOINT`. |
| `metrics_endpoint` | VictoriaMetrics origin or ingest URL; metrics are off without it. Also via `OTEL_VICTORIA_METRICS_ENDPOINT`. |
| `logs_endpoint` | VictoriaLogs origin or ingest URL; logs are off without it. Also via `OTEL_VICTORIA_LOGS_ENDPOINT`. |
| `user` / `user_env`, `password` / `password_env` | Optional Basic auth for all three stores (fallbacks `OTEL_VICTORIA_USER` / `OTEL_VICTORIA_PASSWORD`). |
| `headers` | Extra headers on every signal; a `VL-Stream-Fields` here replaces the default. |
| `metrics` / `logs` / `traces` | Per-signal toggles. Metrics and logs follow their store; an explicit `true` needs the store named. |
| `prometheus_url`, `logs_url`, `query_port`, `query_path`, `search_fetch`, `metrics_match`, `service_name` | Dashboard read path, see below; the first two default to `metrics_endpoint` / `logs_endpoint`. |

No metric-temporality preset: VictoriaMetrics stores cumulative points natively and converts exponential histograms to its `vmrange` histograms.

## Single backend (env vars)

```bash
export OTEL_VICTORIA_ENDPOINT=http://localhost:10428
export OTEL_VICTORIA_METRICS_ENDPOINT=http://localhost:8428
export OTEL_VICTORIA_LOGS_ENDPOINT=http://localhost:9428
export OTEL_PROJECT_NAME=hermes-otel-victoria
```

## What you'll see

- **VictoriaTraces** keeps `service.name` and the span name as stream fields and stores every attribute with a prefix (`span_attr:`, `resource_attr:`, `scope_attr:`). Its Jaeger-compatible API returns the plain Jaeger v1 shape, with every tag typed `string` (`"23571"`, `"false"`), the OTLP status as an `error` tag (`"true"` / `"false"`) and the kind as a `span.kind` tag.
- **VictoriaMetrics**: the bundled stack starts it with `-opentelemetry.usePrometheusNaming`, so the plugin's metrics are stored as `hermes_token_usage_total`, `gen_ai_client_token_usage_sum` / `_count` / `_bucket`, with every resource attribute promoted to a label. Without the flag VictoriaMetrics keeps the dotted OTel names as they are.
- **VictoriaLogs**: one stream `{service.name="hermes-agent"}`, and every log and resource attribute as a flat field with its dots kept (`scope.name`, `hermes.session_id`, `trace_id`, `span_id`, `severity_number`, `severity_text`, `_msg`, `_time`).

## Dashboard adapter

- **Traces** through the Jaeger-compatible API under `/select/jaeger` (`query_path`), the Jaeger v1 grammar: `service` required, `tags` as JSON, `operation` exact, `minDuration` with a unit, `start` / `end` in µs. The adapter coerces the string-typed tags back and reads the status from the `error` tag, so `error="false"` is not an error. Whether `limit=N` keeps the newest traces is not documented, so the adapter asks for `search_fetch` (default 500) and cuts the page itself.
- **Metrics** through the shared Prometheus reader on VictoriaMetrics (`prometheus_url`, default the `metrics_endpoint` origin): range-vector instant queries work, so counters are read as raw cumulative samples with first-sample counting. It expects the Prometheus spelling; a VictoriaMetrics started without the naming flag lists dotted names the curated panels do not match.
- **Logs** through LogsQL on VictoriaLogs (`logs_url`, default the `logs_endpoint` origin): `_time:[start, end)`, `trace_id:"…"`, `span_id:"…"`, `hermes.session_id:"…"`, `scope.name:"…"`, `severity_number:>=N`, `event.name:"…"` / `event.name:*` and `_msg:~"…"` for text, then `| sort by (_time desc) | limit N`; loggers from `| stats by (scope.name) count()`. The Loki helper does not apply: VictoriaLogs has no Loki query API.
- `status = ok` and free text are not expressible on the trace search (the Jaeger grammar has no negative or body search). No "open in" link: vmui's TraceID mode has no documented deep link; set `ui_url` on the entry to offer one.

## Verified behavior

| Check | Result |
|---|---|
| Ingest | `POST` to all three stores answers 200; no headers required |
| Spans | 7 in one trace: `agent` (no references), `llm.nvidia/…`, three `api.nvidia/…`, `tool.terminal`, `tool.read_file`; every tag `string`, `error="false"`, `span.kind=internal` |
| Logs | 28 records in one `{service.name="hermes-agent"}` stream, 12 with a `trace_id`; `scope.name`, `hermes.session_id` as fields |
| Metrics | 27 names in Prometheus spelling (`hermes_token_usage_total`, `hermes_tool_duration_milliseconds_{sum,count,bucket}`, `gen_ai_client_token_usage_*`, …); a range-vector instant query returns raw samples |
| Jaeger API | `tags={"tool.name":"terminal"}` matches on the plain key; `operation=agent`; `minDuration=3s` and `3000ms`; unknown id → 404 `trace not found` |
| LogsQL | `-trace_id:""` 12 rows, `severity_number:>=13` 1, `hermes.session_id:"…"` 18, `_msg:~"API call"` 3 |
| Dashboard verify harness | fresh four-turn batch: **PASS 46 · SKIP 5** (the skips: no UI deep link ×4, `status=ok` not expressible in the Jaeger grammar); token, model-call and tool-call totals and per-trace log lines equal to the live store |
| Resident memory | about 35 MB for the three containers |

## Caveats

- **VictoriaTraces is pre-1.0**: its README says on-disk structures and API endpoints may change. The bundled stack pins `v0.12.0`.
- **Metric naming is a server flag.** The bundled stack sets `-opentelemetry.usePrometheusNaming`; a VictoriaMetrics without it stores dotted names, which the dashboard's Prometheus reader does not treat as counters.
- A 30 s `-search.latencyOffset` on VictoriaMetrics and VictoriaTraces delays the newest samples.
- Limits: VictoriaMetrics 40 labels per series and 4096-char label values; VictoriaLogs and VictoriaTraces 1000 fields per line; VictoriaTraces `-search.maxTraces` 1000 and a 64 MiB request cap.
- Stream cardinality in VictoriaLogs follows `VL-Stream-Fields`; the default the type sends keeps one stream per service.

## Troubleshooting

- **`victoria requires endpoint`** — no `endpoint:` (VictoriaTraces) and no `OTEL_VICTORIA_ENDPOINT`.
- **`victoria metrics need metrics_endpoint`** — `metrics: true` was set without naming VictoriaMetrics.
- **Metrics tab lists dotted names and no counters** — VictoriaMetrics runs without `-opentelemetry.usePrometheusNaming`.
- **Newest turn missing for half a minute** — the latency offset; wait, or lower `-search.latencyOffset` on the store.
