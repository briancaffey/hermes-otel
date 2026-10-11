---
sidebar_position: 8
title: "GreptimeDB"
description: "Send Hermes traces, metrics and logs to GreptimeDB — one open-source database, OTLP in, SQL / PromQL / Jaeger-compatible API out, read back by the dashboard adapter."
---

# GreptimeDB

[GreptimeDB](https://github.com/GreptimeTeam/greptimedb) (Apache-2.0) is a single-binary observability database. It ingests **all three signals** over OTLP/HTTP under a `/v1/otlp` prefix, stores them as SQL tables, and reads them back over SQL, PromQL and a Jaeger-compatible API on the same HTTP port. The plugin's dashboard adapter uses all three read paths.

**Signals:** traces + metrics + logs. **Deployment:** local (single container) · Kubernetes via the official Helm chart · GreptimeCloud. **Cost:** OSS, no account.

:::tip Verified live
One real `hermes` turn through `type: greptime` into the bundled compose stack (GreptimeDB v1.2.1, 2026-10-11) stored 7 spans, 28 log records and 27 metric tables, and the dashboard-verify harness read every signal back through the adapter. See [Verified behavior](#verified-behavior).
:::

## Quick start

```bash
docker compose -f docker-compose/greptime/docker-compose.yaml up -d
```

Then declare the backend (the origin is enough; the type adds the OTLP prefix):

```yaml
backends:
  - type: greptime
    endpoint: http://localhost:4398        # the bundled stack; upstream listens on 4000
capture_logs: true
```

The startup banner reads `✓ GreptimeDB at http://localhost:4398/v1/otlp/v1/traces`. Open http://localhost:4398/dashboard for GreptimeDB's own Traces, Logs and Metrics views, or query it:

```bash
curl -s -X POST --data-urlencode "sql=SELECT span_name, duration_nano FROM opentelemetry_traces ORDER BY timestamp" \
  'http://localhost:4398/v1/sql?db=public'
```

## Why a dedicated `type: greptime`

The three signals need **different headers**: GreptimeDB rejects a trace batch without `X-Greptime-Pipeline-Name: greptime_trace_v1` (`400 Pipeline is required for this API`) and rejects a log batch that carries it (`400 Unsupported pipeline for logs`). The generic OTLP type has one header set for all signals, so it cannot drive GreptimeDB. `type: greptime` asks the plugin to:

1. Complete `endpoint` to `<origin>/v1/otlp/v1/traces` (a bare origin, the `/v1/otlp` prefix or any of the three full URLs all work) and derive the metrics and logs URLs from it.
2. Send the trace pipeline header on traces only, `X-Greptime-DB-Name` on every signal when `db` is set, and Basic auth from `user` / `password` when they resolve.
3. Enable all three signals by default, with cumulative metrics (GreptimeDB stores cumulative and delta points as sent).
4. Enable the [dashboard adapter](/dashboard): traces through the Jaeger-compatible API, metrics and logs through SQL.

## Configuration reference

| Field | Meaning |
|---|---|
| `endpoint` | **Required.** The GreptimeDB HTTP origin, e.g. `http://localhost:4398` (bundled stack) or `http://<host>:4000`. Also via `OTEL_GREPTIME_ENDPOINT`. |
| `db` / `db_env` | Optional database for all three signals (`X-Greptime-DB-Name`; fallback `OTEL_GREPTIME_DB`). Unset means the server default, `public`. |
| `user` / `user_env`, `password` / `password_env` | Optional Basic auth, for a server started with `--user-provider` (fallbacks `OTEL_GREPTIME_USER` / `OTEL_GREPTIME_PASSWORD`). Omitted when either is missing. |
| `headers` | Extra headers on every signal, e.g. `x-greptime-hints: ttl=7d` to age data out. A `X-Greptime-Pipeline-Name` here replaces the default trace pipeline. |
| `metrics` / `logs` / `traces` | Per-signal toggles; all three default **on**. |
| `query_port`, `query_path`, `search_fetch`, `logs_table`, `metrics_match`, `service_name` | Dashboard read path, see below. |

No metric-temporality preset. GreptimeDB **rejects exponential histograms**, so keep the default `metrics_histogram: explicit`.

## Single backend (env vars)

```bash
export OTEL_GREPTIME_ENDPOINT=http://localhost:4398
# export OTEL_GREPTIME_DB=public
export OTEL_PROJECT_NAME=hermes-otel-greptime
```

## What you'll see

- **Traces**: one row per span in `opentelemetry_traces`, with every attribute flattened to a column (`"span_attributes.gen_ai.usage.input_tokens"`, quoted in SQL because of the dots), `parent_span_id` null on the `agent` root, `span_kind`, `span_status_code`, `duration_nano`, `span_events` as JSON. The companion tables `opentelemetry_traces_services` and `_operations` feed the Jaeger-compatible API.
- **Metrics**: one table per metric in Prometheus spelling (`hermes_token_usage_total`, `gen_ai_client_token_usage_sum` / `_count` / `_bucket`), `greptime_timestamp` in milliseconds, `greptime_value`, one TAG column per label (`model`, `token_type`, `service_instance_id`, …).
- **Logs**: `opentelemetry_logs` with `timestamp` (ns), `severity_text` / `severity_number`, `body`, `scope_name` (the Python logger), `trace_id` and `span_id` for records written inside a span, and `log_attributes` as JSON (`hermes.session_id`, `code.file.path`, …).

## Dashboard adapter

The [Hermes dashboard's OTel tab](/dashboard) reads GreptimeDB on the same port the plugin writes to:

- **Traces** through the Jaeger-compatible API under `/v1/jaeger` (`query_path`). It speaks the Jaeger v1 grammar (`service`, `operation` exact, `tags` as JSON, `start` / `end` in µs, `minDuration` with a unit) and returns typed tags, so the Jaeger adapter does the work. Every search names a service (`service_name`, else the plugin's `resource_attributes.service.name`, else `hermes-agent`). Because `limit=N` keeps N *arbitrary* traces rather than the newest, the adapter asks for `search_fetch` (default 500) and cuts the page itself.
- **Metrics** through SQL on the metric tables: raw samples with a five-minute look-behind so counters are turned into per-bucket increases with first-sample counting (GreptimeDB's PromQL route answers a range-vector instant query with an empty body, so SQL is the only way to get raw samples). `group_by` names an attribute; its column is the dotted name with underscores. The metric list comes from PromQL's `label/__name__/values` route, narrowed by `metrics_match`.
- **Logs** through SQL on `opentelemetry_logs` (`logs_table`): trace, span, logger, severity, text, session and event filters, the last two through `json_get_string(log_attributes, '["hermes.session_id"]')` (a bracket path, because a bare dotted path would descend into nested objects).
- `status = ok` and free text are not expressible on the trace search (no negative or body search in the Jaeger grammar). No "open in" link: the embedded dashboard has no documented deep link to one trace; set `ui_url` on the entry to offer one.

## Verified behavior

Read back after one turn (`project_name: greptime-363`, GreptimeDB v1.2.1):

| Check | Result |
|---|---|
| Spans | 7 in `opentelemetry_traces`: `agent` (root), `llm.nvidia/…`, three `api.nvidia/…`, `tool.terminal`, `tool.read_file`; all `SPAN_KIND_INTERNAL`, `STATUS_CODE_OK` |
| Logs | 28 rows; the ones written inside a span carry `trace_id` and `span_id`; `hermes.session_id` in `log_attributes` |
| Metrics | 27 tables: `hermes_token_usage_total`, `hermes_model_usage_total`, `hermes_session_*`, `hermes_tool_duration_milliseconds_{sum,count,bucket}`, `gen_ai_client_token_usage_*`, `gen_ai_client_operation_duration_seconds_*`, `gen_ai_execute_tool_duration_seconds_*`, `gen_ai_agent_token_usage_*`, `hermes_prompt_cache_*`, `hermes_message_count_total` |
| Headers | traces without the pipeline header → 400; logs with it → 400; metrics and logs without it → 200 |
| Jaeger API | `service` required; `start` / `end` in µs, `tags` JSON, `operation` exact, `minDuration=4s` and `4000ms` both accepted; unknown id → 404; `limit` truncates arbitrarily |
| PromQL | `query_range` and `label/__name__/values` work with the plugin's names; a range-vector instant query returns an empty body |
| Dashboard verify harness | fresh four-turn batch: every trace, metric and log check passed once the adapter over-fetched; details in the PR |
| Resident memory | about 95 MB |

## Caveats

- **Pin the image.** v1.3 introduces `greptime_trace_v2` (opt-in, a separate table); the type sends `greptime_trace_v1`.
- **Schema grows with attributes.** Every new attribute key becomes a column; `Int64` may widen to `Float64`. Fine for a laptop; mind it for a long-lived shared instance.
- **No retention by default.** Add `x-greptime-hints: ttl=7d` under `headers:` or `down -v`.
- **Port.** The bundled stack publishes 4398, not upstream's 4000, because dev servers often own 4000 (a Node server on `[::1]:4000` makes `localhost:4000` hang while the container answers on `127.0.0.1`).
- Exponential histograms are rejected; delta points are stored as sent (no conversion), so leave the plugin's cumulative default.

## Troubleshooting

- **`greptime requires endpoint`** — no `endpoint:` and no `OTEL_GREPTIME_ENDPOINT`; there is no default host.
- **`400 Pipeline is required for this API` in the debug log** — the entry is `type: otlp`, not `type: greptime`; or a `headers:` block replaced `X-Greptime-Pipeline-Name` with something GreptimeDB does not know.
- **`400 Unsupported pipeline for logs`** — the trace pipeline header reached `/v1/otlp/v1/logs`; a plugin older than this type, or a custom `logs_headers` is missing.
- **Metrics tab empty in the dashboard** — the metric tables are created on the first export; a one-shot run exports once at exit. Widen the window; the adapter counts a series' first sample.
