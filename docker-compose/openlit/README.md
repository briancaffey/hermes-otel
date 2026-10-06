# OpenLIT

[OpenLIT](https://github.com/openlit/openlit) (Apache-2.0) is an OTel-native LLM
observability stack: ClickHouse plus one app container with the UI and OTLP
ingest. It is the only candidate from #232 that stores **all three signals** on
the standard `/v1/*` paths. Plugin type: `otlp` until `type: openlit` exists
(#222).

## Why pick it
- Traces, metrics and logs with zero auth for local use.
- Reads the GenAI semconv the plugin emits (`gen_ai.request.model`,
  `gen_ai.usage.*`, `gen_ai.tool.*`) and has coding-agent and GPU views.
- Two containers, about 560 MB resident.

## Start / stop
```bash
docker compose -f docker-compose/openlit/docker-compose.yaml up -d
docker compose -f docker-compose/openlit/docker-compose.yaml down -v
```
UI: http://localhost:3010 — `user@openlit.io` / `openlituser` (upstream default).

## Point hermes-otel at it
```yaml
backends:
  - type: otlp
    name: openlit
    endpoint: http://localhost:4338/v1/traces
    # headers: {Authorization: "Bearer <openlit api key>"}   # optional org/project scoping
capture_logs: true
```

## Verify
```bash
docker exec hermes-otel-openlit-clickhouse clickhouse-client --user default --password OPENLIT -d openlit \
  -q "SELECT 'traces',count() FROM otel_traces UNION ALL SELECT 'logs',count() FROM otel_logs UNION ALL SELECT 'hist',count() FROM otel_metrics_histogram"
```
Verified 2026-10-05: 6 spans, 41 logs, 7 sum + 7 histogram metric names from one
Hermes turn.

## Caveats
- **The mounted `otel-collector-config.yaml` is required** with the released
  image (2.1.0, what `latest` resolved to on 2026-10-05): it runs an embedded
  Collector under an OpAMP supervisor that reads `/etc/otel/otel-collector-config.yaml`.
  Without the mount nothing listens on 4317/4318 and exports fail with
  "connection reset by peer". Upstream main replaced the Collector with a
  first-party receiver (PR #1588); when that ships, the mount is simply unused.
- The UI logs `Unknown table … otel_traces` errors until the first span creates
  the tables; harmless.
- `TELEMETRY_ENABLED` is forced to `false` here (upstream defaults it on).
- ClickHouse ports are not published to avoid 8123/9000 collisions with the
  Uptrace stack.
