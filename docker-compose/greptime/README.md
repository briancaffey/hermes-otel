# GreptimeDB (standalone)

[GreptimeDB](https://github.com/GreptimeTeam/greptimedb) (Apache-2.0) is a
single-binary observability database: traces, metrics and logs arrive over
OTLP/HTTP under `/v1/otlp`, land in SQL tables, and come back through SQL,
PromQL and a Jaeger-compatible API, all on one HTTP port. It fills the "one
database, SQL over everything" slot that only the non-OSS Sigiro and Maple
stacks covered. Explicit plugin type: `greptime` (#363). Pinned v1.2.1
(2026-09-16); v1.3 adds an opt-in `greptime_trace_v2` model into a separate
table, so stay on v1 until the adapter learns it.

## Why pick it
- One container, about 95 MB resident after a turn, no login, no account.
- All three signals, and the dashboard adapter reads all three back (traces
  through `/v1/jaeger`, metrics and logs through SQL).
- Span attributes become real columns (`span_attributes.gen_ai.usage.input_tokens`),
  metrics one table per name in Prometheus spelling, so plain SQL answers
  "how many tokens did this session use" in one line.

## Start / stop
```bash
docker compose -f docker-compose/greptime/docker-compose.yaml up -d
docker compose -f docker-compose/greptime/docker-compose.yaml down -v
```
UI: http://localhost:4398/dashboard (no login). Healthy about 10 s after start.
Host port **4398** (upstream 4000): dev servers love 4000, and a Node server
on `[::1]:4000` makes `localhost:4000` hang while the container answers on
127.0.0.1, so the folder moves it into the repo's `43x8` range.

## Point hermes-otel at it
```yaml
backends:
  - type: greptime
    endpoint: http://localhost:4398        # the type appends /v1/otlp/v1/traces
capture_logs: true
```
The banner reads `✓ GreptimeDB at http://localhost:4398/v1/otlp/v1/traces`. The
type sends `X-Greptime-Pipeline-Name: greptime_trace_v1` on traces **only**
(GreptimeDB rejects traces without it and logs with it), `X-Greptime-DB-Name`
from `db:` when set, and Basic auth from `user` / `password` when the server
runs a user provider.

## Verify
```bash
curl -s -X POST --data-urlencode "sql=SELECT span_name, parent_span_id, duration_nano FROM opentelemetry_traces ORDER BY timestamp" \
  'http://localhost:4398/v1/sql?db=public'
curl -s -X POST --data-urlencode "sql=SELECT count(*) FROM opentelemetry_logs" 'http://localhost:4398/v1/sql?db=public'
curl -s 'http://localhost:4398/v1/prometheus/api/v1/label/__name__/values'
curl -s 'http://localhost:4398/v1/jaeger/api/traces?service=hermes-agent&limit=5' | head -c 400
```
Verified 2026-10-11 (v1.2.1, hermes-otel 1.24.0 branch): one Hermes turn
stored 7 spans in `opentelemetry_traces` (the `agent` root with
`parent_span_id` null, `llm.*`, three `api.*`, `tool.terminal`,
`tool.read_file`), 28 rows in `opentelemetry_logs` with `trace_id` and
`span_id` on the ones written inside a span, and 27 metric tables
(`hermes_token_usage_total`, `hermes_tool_duration_milliseconds_{sum,count,bucket}`,
`gen_ai_client_token_usage_*`, …). The dashboard-verify harness passed every
check it could run (see the docs page).

## Caveats
- **Traces need the pipeline header.** `POST /v1/otlp/v1/traces` without
  `X-Greptime-Pipeline-Name: greptime_trace_v1` is `400 Pipeline is required
  for this API`; the same header on `/v1/otlp/v1/logs` is `400 Unsupported
  pipeline for logs`. The `greptime` type sends it on traces only; a generic
  `otlp` entry cannot (one header set for all signals).
- **Attributes are columns.** Every new attribute key adds a column to
  `opentelemetry_traces` (quote them in SQL: `"span_attributes.tool.name"`);
  `Int64` may widen to `Float64` when a later value is fractional. The trace
  table is partitioned 16 ways on the first character of `trace_id`.
- **Metrics in Prometheus spelling**: dots become underscores, counters get
  `_total`, histograms split into `_sum` / `_count` / `_bucket` tables with
  `greptime_timestamp` (ms) and `greptime_value`. The PromQL endpoint works
  for `query_range`, but a range-vector instant query (`name[1h]`) answers an
  empty body, which is why the adapter reads raw samples with SQL.
  **Exponential histograms are rejected**; keep `metrics_histogram: explicit`.
- `/v1/jaeger/api/traces?limit=N` keeps N arbitrary traces, not the newest;
  the adapter over-fetches (`search_fetch`, default 500) and cuts the page.
- No retention by default: add `x-greptime-hints: ttl=7d` under `headers:` to
  age data out, or `down -v`.
- The embedded dashboard has Traces, Logs and Metrics views but no documented
  deep link to one trace, so the plugin dashboard offers no "open in" link
  unless `ui_url` is set on the entry.
