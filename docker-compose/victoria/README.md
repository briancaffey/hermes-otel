# VictoriaTraces + VictoriaLogs + VictoriaMetrics

The [VictoriaMetrics](https://docs.victoriametrics.com) family (all
Apache-2.0) covers the three signals with three single-binary stores that each
ingest OTLP/HTTP directly: [VictoriaTraces](https://docs.victoriametrics.com/victoriatraces/)
v0.12.0 (pre-1.0), [VictoriaLogs](https://docs.victoriametrics.com/victorialogs/)
v1.53.0 and [VictoriaMetrics](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/)
v1.153.0. VictoriaTraces speaks the Jaeger query API and VictoriaMetrics the
Prometheus API, so the dashboard reads two of the three through adapters it
already had. Explicit plugin type: `victoria` (#366).

## Why pick it
- Three tiny containers (about 35 MB resident in total), no login, no account.
- All three signals, each in the store built for it, and the dashboard adapter
  reads all three back.
- The Prometheus-family store that is not Prometheus: MetricsQL, `vmui`, and a
  `-retentionPeriod` per store.

## Start / stop
```bash
docker compose -f docker-compose/victoria/docker-compose.yaml up -d
docker compose -f docker-compose/victoria/docker-compose.yaml down -v
```
UIs (no login): traces http://localhost:10428/select/vmui, logs
http://localhost:9428/select/vmui/, metrics http://localhost:8428/vmui. Each
store answers `GET /health` with `OK` within a few seconds; the images are
built from scratch (no shell), so Compose cannot health-check them.

## Point hermes-otel at it
```yaml
backends:
  - type: victoria
    endpoint: http://localhost:10428          # VictoriaTraces → /insert/opentelemetry/v1/traces
    metrics_endpoint: http://localhost:8428   # VictoriaMetrics → /opentelemetry/v1/metrics
    logs_endpoint: http://localhost:9428      # VictoriaLogs → /insert/opentelemetry/v1/logs
logs:
  capture: true   # send Python log lines as OTel logs too
```
The banner reads `✓ Victoria at http://localhost:10428/insert/opentelemetry/v1/traces`.
A signal whose store is not named is off (there is no host to derive it on).

## Verify
```bash
curl -s 'http://localhost:10428/select/jaeger/api/traces?service=hermes-agent&limit=5' | head -c 400
curl -s 'http://localhost:8428/api/v1/label/__name__/values' | head -c 400
curl -s -X POST http://localhost:9428/select/logsql/query -d 'query=*' -d 'limit=3' -d 'start=1h' -d 'end=now'
```
Verified 2026-10-11 (VictoriaTraces v0.12.0, VictoriaLogs v1.53.0,
VictoriaMetrics v1.153.0, hermes-otel 1.24.0 branch): one Hermes turn stored
one trace of 7 spans (`agent` root, `llm.*`, three `api.*`, `tool.terminal`,
`tool.read_file`), 28 log records in one `{service.name="hermes-agent"}` stream
(12 with a `trace_id`), and 27 metric names in Prometheus spelling
(`hermes_token_usage_total`, `hermes_tool_duration_milliseconds_{sum,count,bucket}`, …).
Metrics appear in VictoriaMetrics about 30 s after export (`-search.latencyOffset`).

## Caveats
- **VictoriaTraces is pre-1.0**: its README says on-disk structures and API
  endpoints may change. Pinned `v0.12.0`; `down -v` when you bump it.
- **Metric names**: the folder starts VictoriaMetrics with
  `-opentelemetry.usePrometheusNaming`, so OTLP names become
  `hermes_token_usage_total` / `…_seconds_sum`, the spelling the dashboard's
  Prometheus reader and the LGTM stack use. Without the flag VictoriaMetrics
  keeps the dotted OTel names "as is".
- **Everything is a string in VictoriaTraces**: every Jaeger tag comes back
  typed `string` (`"23571"`, `"false"`); the OTLP status is an `error` tag
  (`"true"` / `"false"`) and the kind a `span.kind` tag. The adapter coerces
  values back and reads the status from `error`.
- **Log streams**: VictoriaLogs keys streams on every resource attribute by
  default, and `service.instance.id` changes with each Hermes process. The
  `victoria` type sends `VL-Stream-Fields: service.name` so one stream holds
  the service; `headers:` overrides it. Field names keep their dots
  (`scope.name`, `hermes.session_id`).
- A 30 s `-search.latencyOffset` on VictoriaMetrics and VictoriaTraces delays
  visibility of the newest samples; a one-shot run shows up half a minute later.
- Limits worth knowing: VictoriaMetrics 40 labels per series and 4096-char
  label values; VictoriaLogs and VictoriaTraces 1000 fields per line;
  VictoriaTraces `-search.maxTraces` 1000.
- No "open in" link per trace: vmui's Trace Explorer has a TraceID mode but
  no documented deep link, and the adapter ignores `ui_url` for trace links
  (`ui_url` only points the Settings card's backend name at vmui).
- Ports 10428 / 9428 / 8428 are upstream's and collide with nothing else here.
