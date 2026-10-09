# Jaeger v1 (all-in-one)

[Jaeger](https://www.jaegertracing.io) (Apache-2.0) is the classic distributed
tracing UI. This folder runs `jaegertracing/all-in-one`, the **v1** line that
ended with 1.76.0 in December 2025; it still serves the classic query API
(`/api/traces`, `/api/services`) that the plugin dashboard's Jaeger adapter uses.
For the current v2 binary see [`../jaeger-v2`](../jaeger-v2). Explicit plugin
type: `jaeger`.

## Why pick it
- One tiny container (about 25 MB resident), in-memory storage, no login.
- The dashboard's Jaeger adapter reads it back (it does not read v2 yet, #245).
- Good for a plain span-tree look when you do not need LLM-specific panels.

## Start / stop
```bash
docker compose -f docker-compose/jaeger/docker-compose.yaml up -d
docker compose -f docker-compose/jaeger/docker-compose.yaml down
```
UI: http://localhost:16686 (no login).

## Point hermes-otel at it
```yaml
backends:
  - type: jaeger
    endpoint: http://localhost:4318/v1/traces
```

## Verify
```bash
curl -s 'http://localhost:16686/api/traces?service=hermes-agent&limit=1' | head -c 400
```

## Caveats
- Traces only; metrics and logs are off for the `jaeger` type.
- Binds **4318**, the same OTLP/HTTP port as the LGTM stack; run one of the two.
- In-memory: everything is gone when the container stops.
- `all-in-one:latest` will never move past 1.76.0; upstream development is on v2.
