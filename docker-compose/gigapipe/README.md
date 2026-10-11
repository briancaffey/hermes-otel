# Gigapipe (formerly qryn)

[Gigapipe](https://github.com/metrico/gigapipe) (AGPL-3.0, v5.5.3 on
2026-09-30; `metrico/qryn` redirects there) is a polyglot store on ClickHouse:
OTLP for **traces, metrics and logs** comes in on one port, and the Tempo,
Prometheus and Loki query APIs go out of the same port. The plugin dashboard
therefore reads it with the adapters it already had for the LGTM stack, with a
few Gigapipe-specific detours. Explicit plugin type: `gigapipe` (#370).

## Why pick it
- Two containers (Gigapipe about 16 MB, ClickHouse about 300 MB), no login.
- Grafana-compatible by construction: point Grafana's Tempo, Prometheus and
  Loki data sources at the one port.
- A second implementation of the three Grafana APIs, which is what makes it
  interesting for testing the dashboard adapters.

## Start / stop
```bash
docker compose -f docker-compose/gigapipe/docker-compose.yaml up -d      # healthy in ~10 s
docker compose -f docker-compose/gigapipe/docker-compose.yaml down -v
```
UI: http://localhost:3110 (the built-in qryn-view, no login). Host port 3110
because the LGTM stack's Loki owns 3100 here; ClickHouse is not published
(8123 / 9000 belong to the Uptrace stack).

## Point hermes-otel at it
```yaml
backends:
  - type: gigapipe
    endpoint: http://localhost:3110        # the type appends /v1/traces; metrics and logs derived
capture_logs: true
```
The banner reads `✓ Gigapipe at http://localhost:3110/v1/traces`. The dashboard
adapter defaults every read API to the same origin (`prometheus_url`,
`loki_url` and `query_port` override).

## Verify
```bash
curl -s -G 'http://localhost:3110/api/search' --data-urlencode 'q={ nestedSetParent < 0 }' -d limit=5 | head -c 400
curl -s 'http://localhost:3110/api/v1/label/__name__/values' | head -c 400
curl -s -G 'http://localhost:3110/loki/api/v1/query_range' --data-urlencode 'query={service_name="hermes-agent"}' -d limit=3 -d direction=backward | head -c 400
```
Verified 2026-10-11 (Gigapipe v5.5.3, ClickHouse 24.9.1, hermes-otel 1.24.0
branch): one Hermes turn stored one trace of 7 spans, 28 log lines (12 with a
`trace_id` label) and 27 metric names in Prometheus spelling
(`hermes_token_usage_total`, `…_seconds_sum` / `_count` / `_bucket`).

## Caveats
- **TraceQL `select()` returns no attributes**: a search answers one row per
  trace but an empty attribute list, so the adapter fetches the trace detail
  for each card. Without a TraceQL `q`, `/api/search` lists one row per *span*.
- **The trace detail is pre-1.0 OTLP JSON**: `resourceSpans` holding
  `instrumentationLibrarySpans`, every attribute a `stringValue`, no `kind`, the
  resource reduced to `collector=gigapipe` with the service on each span's
  `serviceName`. The adapter reshapes it and coerces the values back.
- **Range-vector instant queries answer empty** (`hermes_token_usage_total[1h]`
  → no result), so counters are read with `query_range` at the 15 s rollup step
  and turned into increases by the adapter. PromQL is served from the
  `metrics_15s` rollup; raw samples live 7 days (`SAMPLES_DAYS`). Known bug
  [#1027](https://github.com/metrico/gigapipe/issues/1027): a coarse,
  unaligned `query_range` can come back empty.
- **Delta metric points are rejected** one by one (`partial_success`); the
  plugin's cumulative default is the right one.
- **Loki streams are labels only**: every attribute becomes a label with dots
  turned into underscores (`hermes_session_id`, `trace_id`, `span_id`), the
  level is a `level` label (no `severity_number`), and there is no scope
  (logger) label, so the Logs tab cannot filter by logger or list loggers.
- Trace ids come back upper-case from a tag search and lower-case from a
  TraceQL one; the adapter lower-cases and pads them.
- AGPL-3.0; the ClickHouse user `gigapipe` / `gigapipe-local` is a local default.
