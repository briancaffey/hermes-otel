# Zipkin (with OTLP ingest)

[Zipkin](https://zipkin.io) (Apache-2.0) is the original distributed-tracing
UI. The stock `openzipkin/zipkin` image has **no OTLP endpoint**; this folder
runs [`zipkin-otel`](https://github.com/openzipkin-contrib/zipkin-otel), the
contrib module published as its own image on top of the full server (0.3.0 =
Zipkin 3.6.0, 2026-03-25). It serves `POST /v1/traces` on the query port next
to the UI and API v2. Explicit plugin type: `zipkin` (#364).

## Why pick it
- One container, about 300 MB resident, in-memory storage, no login.
- The simplest read API in the repo (`/api/v2/traces`, `/api/v2/trace/{id}`);
  the dashboard's Zipkin adapter reads it, filters included.
- A plain span tree with every attribute as a tag; nothing LLM-specific.

## Start / stop
```bash
docker compose -f docker-compose/zipkin/docker-compose.yaml up -d
docker compose -f docker-compose/zipkin/docker-compose.yaml down
```
UI: http://localhost:9411/zipkin (no login). Healthy about 20 s after start
(`docker inspect --format '{{.State.Health.Status}}' hermes-otel-zipkin`).

## Point hermes-otel at it
```yaml
backends:
  - type: zipkin
    endpoint: http://localhost:9411        # the type appends /v1/traces
```
The startup banner reads `✓ Zipkin at http://localhost:9411/v1/traces (traces only)`.

## Verify
```bash
curl -s 'http://localhost:9411/api/v2/traces?serviceName=hermes-agent&limit=1' | head -c 400
curl -s http://localhost:9411/health          # OpenTelemetryHttpCollector{} must be UP
```
Verified 2026-10-11 (zipkin-otel 0.3.0, hermes-otel 1.24.0 branch): one Hermes
turn stored one trace of 7 spans (`agent` root, `llm.*`, three `api.*`,
`tool.terminal`, `tool.read_file`); `/v1/metrics` answered 404 and `/v1/logs`
202 with nothing stored.

## Caveats
- **Use the `zipkin-otel` image.** `openzipkin/zipkin` and `zipkin-slim`
  return nothing on `/v1/traces`. The module is marked experimental upstream
  and needs 128-bit trace ids, which OTLP always sends.
- Traces only: the `zipkin` type keeps metrics and logs off. A `/v1/logs`
  record is kept only as an annotation on a span it can name through an
  `event.name` attribute, which the plugin's records do not carry.
- Every attribute is a **string** tag (`"7589"`, `"true"`), arrays lose their
  brackets, span links are dropped and INTERNAL spans have no kind. The
  dashboard adapter coerces numbers and booleans back on read.
- A span without `service.name` has no `localEndpoint` and never shows up in
  search; the plugin always sets one.
- In-memory: everything is gone when the container stops (`MEM_MAX_SPANS`
  500000). The UI searches the last 15 minutes by default; widen the lookback.
- Port 9411 collides with nothing else in this directory.
