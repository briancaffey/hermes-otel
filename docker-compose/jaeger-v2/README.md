# Jaeger v2

The OpenTelemetry-Collector-based [Jaeger v2](https://www.jaegertracing.io)
binary (`jaegertracing/jaeger`, Apache-2.0), which is what the official Helm
chart deploys. Traces only, in-memory. Explicit plugin type: `jaeger`.

## Why pick it
- Same UI as v1, current release line (2.21.0 pinned here).
- Reproduces issue #245 on a laptop: the dashboard's Jaeger adapter speaks the
  v1 API, which v2 does not serve.
- Can run next to the v1 stack (different host ports).

## Start / stop
```bash
docker compose -f docker-compose/jaeger-v2/docker-compose.yaml up -d
docker compose -f docker-compose/jaeger-v2/docker-compose.yaml down
```
UI: http://localhost:16696 (no login).

## Point hermes-otel at it
```yaml
backends:
  - type: jaeger
    endpoint: http://localhost:4368/v1/traces
```

## Verify
```bash
curl -s http://localhost:16696/api/v3/services
curl -s 'http://localhost:16696/api/v3/traces?query.service_name=hermes-agent&query.start_time_min=2026-01-01T00:00:00Z&query.start_time_max=2027-01-01T00:00:00Z' | head -c 400
```

## Caveats
- Verified 2026-10-05: `/api/services` and `/api/traces` are **404** on 16686;
  only `/api/v3/...` (gRPC-gateway of the v3 QueryService, OTLP-JSON shaped)
  answers. The plugin dashboard cannot read v2 until #245 lands.
- In-memory storage; traces are lost on restart.
- Traces only.
