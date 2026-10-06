# Maple Local

[Maple](https://maple.dev/local/) ships Maple Local as a single binary (brew /
curl installer) with an embedded ClickHouse (chDB): traces, metrics, logs,
errors, sessions and a service map on one port. Source-available under
**FSL-1.1-ALv2** (converts to Apache-2.0 after two years), not OSI open source.
This folder builds a small image from the GitHub release tarball so it runs like
the other stacks. Issue #49. Plugin type: `otlp`.

## Why pick it
- All three signals accepted on standard paths with no auth, one container,
  about 340 MB resident.
- Bundled UI (`--offline`) and a SQL endpoint over the embedded ClickHouse.
- Has an AI trace index (model, tokens, cost, tool name columns) aimed at agent
  telemetry.

## Start / stop
```bash
docker compose -f docker-compose/maple/docker-compose.yaml up -d --build
docker compose -f docker-compose/maple/docker-compose.yaml down -v
```
UI: http://localhost:4388 (no login). Same port for OTLP/HTTP.

## Point hermes-otel at it
```yaml
backends:
  - type: otlp
    name: maple
    endpoint: http://localhost:4388/v1/traces
capture_logs: true
```

## Verify
```bash
curl -s -X POST http://localhost:4388/local/query -H 'content-type: application/json' \
  -d '{"sql":"SELECT SpanName, count(*) FROM traces GROUP BY 1"}'
curl -s -X POST http://localhost:4388/local/query -H 'content-type: application/json' \
  -d '{"sql":"SELECT count(*) FROM logs UNION ALL SELECT count(*) FROM metrics_histogram"}'
```
Verified 2026-10-05: 6 spans, 40 logs, 40 sum + 42 histogram rows from one turn.

## Caveats
- The binary dlopens `libchdb.so` from beside itself; the Dockerfile installs
  both files into `/root/.maple/bin` the way the upstream installer does.
  Without the library it loops on "libchdb not found".
- `--host 0.0.0.0` is required inside a container and, per Maple, "exposes
  unauthenticated ingest and queries" — fine on a laptop, not on a shared host.
- The `ai_trace_index` view was still empty 6 s after ingest, so whether Maple's
  AI views pick up the plugin's attributes is unverified.
- Column names are PascalCase (`TraceId`, `SpanName`, `ServiceName`).
- Pinned to release v0.0.23 via `MAPLE_VERSION`; the Dockerfile supports
  amd64 and arm64.
